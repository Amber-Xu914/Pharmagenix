"""Run workflow stages 2–4 for one gene or drug and normalise the result into one evidence bundle.

The bundle is stored on the Query row and drives every output:
  trace                  -> what each stage did, shown as the workflow stepper on the results page
  outcome                -> the "interactions found?" decision
  normalisation          -> how the input was resolved (P53 -> TP53)
  items                  -> numbered evidence (E1, E2, ...) the LLM must cite, including the gene profile
  table                  -> one graded row per gene–drug pair (stage 5)
  network                -> Cytoscape.js elements for the relationship graph
  australian_availability-> PBS listing per drug (context only; not DGI evidence, not sent to the LLM)
  source_status          -> which sources succeeded / failed / were skipped, with data versions
"""

import logging

from flask import current_app

from ... import tracking

from ...reference import lookup
from ...reference.normalise import resolve_entities
from ...reference.text import strip_salt
from ..relations import categorize, matches
from ..workflow.evidence import consolidate_pairs, decide_outcome
from ..workflow.profiles import build_drug_profile, build_gene_profile
from .dgidb import DGIdbClient
from .drugbank import DrugBankClient
from .pharmgkb import PharmGKBClient
from .pubtator import PubTatorClient
from .uniprot import UniProtClient

from ..relations import matches
from .. import safety  # module import: safety imports this package too
from .. import genotypes as pgx_genotypes

logger = logging.getLogger(__name__)

NOT_IMPORTED = "not imported yet; run: flask --app run import-data"


def _safe(status, name, fn, version=None):
    """Run one source; record the outcome instead of letting one failure sink the whole query."""
    with tracking.span(f"source:{name}", span_type="RETRIEVER") as s:
        try:
            result = fn()
        except Exception as exc:  # noqa: BLE001 - any source failure should be reported, not raised
            logger.warning("Source %s failed: %s", name, exc)
            status[name] = {"state": "error", "error": str(exc)[:300], "count": 0}
            tracking.set_outputs(s, status[name])
            return []
        status[name] = {"state": "ok", "count": len(result)}
        if version:
            status[name]["version"] = version
        tracking.set_outputs(s, status[name])
        return result


def _local(status, releases, name, fn):
    """Like _safe, for a local dataset that may not have been imported yet."""
    release = releases.get(name)
    if release is None:
        status[name] = {"state": "disabled", "count": 0, "error": NOT_IMPORTED}
        return []
    return _safe(status, name, fn, version=release.version)


def collect_evidence(name, relation_types, resolved=None, entity_type="gene", medications=(), genotypes=()):
    """name: what the user typed; entity_type: "gene" (gene -> drugs) or "drug" (drug -> genes).
    resolved: its stage 1 entry (explorer.py has already checked it). medications: the patient's current
    medications (names), checked for drug–drug interactions with the drugs found (services/safety.py).
    genotypes: the patient's pharmacogene results ([{"gene", "value"}]), turned into CPIC recommendations for the
    drugs found (services/genotypes.py)."""
    cfg = current_app.config
    timeout = cfg["HTTP_TIMEOUT"]
    status = {}
    releases = lookup.latest_releases()
    wants_snp = not relation_types or "snp" in relation_types

    # Stage 1: entity resolution against HGNC (genes) or ChEMBL / PBS names (drugs). A name that isn't found is
    # searched as typed.
    entry = resolved or resolve_entities([name], entity_type)[0]
    symbol = entry["name"]
    is_drug = entity_type == "drug"
    genes, drugs = ([], [symbol]) if is_drug else ([symbol], [])
    drug_ids = [entry["id"]] if is_drug and entry.get("id") else []

    # Stage 3a: DGIdb, the broad list of drug–gene interactions for the gene (or the drug).
    dgidb = DGIdbClient(cfg["DGIDB_URL"], timeout)
    interactions = _safe(status, "dgidb", lambda: dgidb.fetch_interactions(genes=genes, drugs=drugs))
    interactions = _filter(_merge_salt_forms(interactions), "interaction_types", relation_types)
    interactions.sort(key=lambda it: -(it["score"] or 0))
    interactions = interactions[: cfg["MAX_INTERACTIONS"]]
    if status["dgidb"]["state"] == "ok":
        status["dgidb"]["count"] = len(interactions)

    # Stage 3b: ChEMBL (local), curated mechanisms with action types.
    mechanisms = _local(status, releases, "chembl", lambda: lookup.chembl_mechanisms(
        genes, drug_ids, limit=cfg["MAX_MECHANISMS"]))
    mechanisms = _filter(mechanisms, "action_type", relation_types)
    if status["chembl"]["state"] == "ok":
        status["chembl"]["count"] = len(mechanisms)

    # Stage 3b': FDA CYP / transporter roles (local): for a CYP or transporter gene, its clinical inhibitors,
    # inducers and sensitive substrates (CYP2D6 -> paroxetine, strong inhibitor); for a drug, the pathways it affects.
    cyp_roles = _local(status, releases, "fda_ddi", lambda: (
        lookup.cyp_roles_for_drug(drug_ids[0] if drug_ids else None, symbol) if is_drug
        else lookup.cyp_roles_for_genes(genes)))
    cyp_roles = [r for r in cyp_roles if r["gene"] and matches(_role_categories(r), relation_types)]
    if status["fda_ddi"]["state"] == "ok":
        status["fda_ddi"]["count"] = len(cyp_roles)

    # Stage 3c: SNP-specific response, CPIC guidelines (local) and PharmGKB/ClinPGx clinical annotations (live).
    guidelines, variants = [], []
    if wants_snp:
        guidelines = _local(status, releases, "cpic", lambda: lookup.cpic_guidelines(
            genes, drugs, drug_ids, limit=cfg["MAX_GUIDELINES"]))
        pgkb = PharmGKBClient(cfg["PHARMGKB_URL"], timeout)
        variants = _safe(status, "pharmgkb", lambda: pgkb.clinical_annotations(
            genes=genes, drugs=drugs, limit=cfg["MAX_VARIANT_ANNOTATIONS"]))
    else:
        status["cpic"] = status["pharmgkb"] = {"state": "skipped", "count": 0}

    # Stage 3d: DrugBank, mechanism text for the top drugs (licensed; skipped without a key).
    drugbank = DrugBankClient(cfg["DRUGBANK_URL"], cfg["DRUGBANK_API_KEY"], timeout)
    drug_info = []
    if drugbank.enabled:
        top_drugs = drugs or _unique([it["drug"] for it in interactions])[:10]
        drug_info = _safe(status, "drugbank", lambda: drugbank.lookup_drugs(top_drugs))
    else:
        status["drugbank"] = {"state": "disabled", "count": 0, "error": "DRUGBANK_API_KEY not set"}

    # Stage 3e: PubTator, literature for the strongest gene–drug pairs.
    pubtator = PubTatorClient(cfg["PUBTATOR_URL"], timeout)
    lit_pairs = [(it["gene"], it["drug"]) for it in interactions] or [(m["gene"], m["drug"]) for m in mechanisms]
    publications = _safe(status, "pubtator", lambda: pubtator.search_pairs(
        list(dict.fromkeys(lit_pairs)), [symbol], limit_total=cfg["MAX_PUBLICATIONS"]))

    # Australian context: PBS (local) listing for the most relevant drugs.
    pbs_drugs = _pbs_candidates(guidelines, mechanisms, interactions, cfg["MAX_PBS_DRUGS"])
    if is_drug:  # the searched drug first, whatever else is listed
        pbs_drugs = [(symbol, entry.get("id"))] + [d for d in pbs_drugs if strip_salt(d[0]) != strip_salt(symbol)]
        pbs_drugs = pbs_drugs[: cfg["MAX_PBS_DRUGS"]]
    availability = _local(status, releases, "pbs", lambda: lookup.pbs_availability(pbs_drugs))
    if status["pbs"]["state"] == "ok":
        status["pbs"]["count"] = sum(1 for a in availability if a["pbs_listed"])
    elif status["pbs"]["state"] == "disabled":
        availability = None

    # Stage 2: the profile, gene (HGNC + UniProt) or drug (ChEMBL).
    profile = (build_drug_profile(entry) if is_drug
               else build_gene_profile(entry, UniProtClient(cfg["UNIPROT_URL"], timeout), status))

    items = build_evidence_items(interactions, variants, drug_info, publications, mechanisms, guidelines,
                                 releases, profiles=[profile], cyp_roles=cyp_roles)

    # Stage 4: merge per pair, grade, flag conflicts, decide "interactions found?".
    with tracking.span("evidence_integration") as s:
        table = consolidate_pairs(items)
        outcome = decide_outcome(items, status)
        tracking.set_outputs(s, {"pairs": len(table), "outcome": outcome["status"]})

    # Safety: ChEMBL warnings on every drug listed, and drug–drug interactions with the current medications.
    with tracking.span("safety", inputs={"medications": list(medications)}) as s:
        safety_block = safety.check_table(table, entry if is_drug else None, list(medications), items, status)
        tracking.set_outputs(s, {"interactions": len(safety_block["interactions"])})

    # The patient's genotypes: phenotype and the matching CPIC recommendation for each drug of the result.
    genotype_block = None
    if genotypes:
        with tracking.span("genotypes", inputs={"genotypes": list(genotypes)}) as s:
            drugs_found = [{"name": r["drug"], "chembl_id": r.get("chembl_id")} for r in table]
            if is_drug:
                drugs_found.insert(0, {"name": symbol, "chembl_id": entry.get("id")})
            cpic_release = releases["cpic"].version if "cpic" in releases else None
            if cpic_release is None:
                status.setdefault("cpic", {"state": "disabled", "count": 0, "error": NOT_IMPORTED})
            genotype_block = pgx_genotypes.check(list(genotypes), drugs_found, safety_block["medications"], items,
                                                 status, cpic_release)
            by_id = {i["id"]: i for i in items}
            for row in table:
                key = strip_salt(row["drug"]).upper()
                row["genotype"] = [{"evidence_id": eid, "phenotype": by_id[eid]["phenotype"],
                                    "classification": by_id[eid]["classification"],
                                    "population": by_id[eid]["population"],
                                    "summary": by_id[eid]["short_recommendation"]}
                                   for eid in genotype_block["recommendations"]
                                   if strip_salt(by_id[eid]["drug"]).upper() == key]
            tracking.set_outputs(s, {"recommendations": len(genotype_block["recommendations"])})

    return {
        "outcome": outcome,
        "trace": _trace(entry, status, table, outcome, entity_type),
        "normalisation": [entry],
        "items": items,
        "table": table,
        "network": build_network(items, [symbol]),
        "australian_availability": availability,
        "safety": safety_block,
        "genotypes": genotype_block,
        "source_status": status,
        "counts": {
            "interactions": len(interactions),
            "mechanisms": len(mechanisms),
            "guidelines": len(guidelines),
            "variant_annotations": len(variants),
            "publications": len(publications),
            "drug_info": len(drug_info),
            "fda_cyp_roles": len(cyp_roles),
            "pairs": len(table),
        },
    }


SOURCE_LABELS = {"dgidb": "DGIdb", "chembl": "ChEMBL", "cpic": "CPIC", "pharmgkb": "ClinPGx",
                 "drugbank": "DrugBank", "pubtator": "PubTator", "uniprot": "UniProt"}


def _trace(entry, status, table, outcome, entity_type="gene"):
    """One line per workflow stage for the results page. Stages 5–6 are added by the frontend from the reports."""
    def sources(keys):
        parts = []
        for key in keys:
            s = status.get(key, {})
            if s.get("state") == "ok":
                parts.append(f"{SOURCE_LABELS[key]} {s['count']}")
            elif s.get("state") == "error":
                parts.append(f"{SOURCE_LABELS[key]} failed")
        return " · ".join(parts) or "no sources available"

    strong = sum(1 for r in table if r["grade"] == "strong")
    n_conflicts = sum(1 for r in table if r["conflicts"])
    return [
        {"stage": "resolution", "status": "done" if entry["type"] else "warning",
         "detail": f"{entry['input']} → {entry['name']}" if entry["type"] else f"{entry['input']} searched as typed"},
        {"stage": "profiles", "status": "done", "detail": "drug profile · ChEMBL" if entity_type == "drug"
         else f"gene profile · {sources(['uniprot'])}"},
        {"stage": "retrieval", "status": "done",
         "detail": sources(["dgidb", "chembl", "cpic", "pharmgkb", "pubtator", "drugbank"])},
        {"stage": "evidence", "status": "done" if outcome["status"] != "none" else "warning",
         "detail": f"{len(table)} gene–drug pairs · {strong} strong · {n_conflicts} with conflicts"
                   if outcome["status"] != "none" else "no known interaction"},
    ]


def _filter(rows, type_field, relation_types):
    kept = []
    for row in rows:
        types = row[type_field] if isinstance(row[type_field], list) else [row[type_field]]
        row["categories"] = categorize([t for t in types if t])
        if matches(row["categories"], relation_types):
            kept.append(row)
    return kept


def _merge_salt_forms(interactions):
    """DGIdb lists salt forms as separate drugs (CLOPIDOGREL BESILATE, ... BISULFATE); merge them."""
    merged = {}
    for it in interactions:
        it["drug"] = strip_salt(it["drug"])
        key = (it["gene"], it["drug"])
        if key not in merged:
            merged[key] = it
            continue
        kept = merged[key]
        kept["interaction_types"] = list(dict.fromkeys(kept["interaction_types"] + it["interaction_types"]))
        kept["sources"] = sorted(set(kept["sources"]) | set(it["sources"]))
        kept["pmids"] = list(dict.fromkeys(kept["pmids"] + it["pmids"]))
        kept["score"] = max(kept["score"] or 0, it["score"] or 0)
        kept["drug_approved"] = kept.get("drug_approved") or it.get("drug_approved")
    return list(merged.values())


def _unique(values):
    return list(dict.fromkeys(v for v in values if v))


def _pbs_candidates(guidelines, mechanisms, interactions, limit):
    candidates = (
        [g["drug"].upper() for g in guidelines]
        + [m["drug"] for m in mechanisms if (m.get("max_phase") or 0) >= 4]
        + [it["drug"] for it in interactions if it.get("drug_approved")]
    )
    names, seen = [], set()
    for name in candidates:
        if name and strip_salt(name) not in seen:
            seen.add(strip_salt(name))
            names.append(name)
    chembl_ids = {g["drug"].upper(): g.get("chembl_id") for g in guidelines}
    chembl_ids.update({m["drug"]: m["chembl_id"] for m in mechanisms})
    return [(name, chembl_ids.get(name)) for name in names[:limit]]


def build_evidence_items(interactions, variants, drug_info, publications, mechanisms=(), guidelines=(),
                         releases=None, profiles=(), cyp_roles=()):
    releases = releases or {}
    items = []

    def add(item):
        item["id"] = f"E{len(items) + 1}"
        items.append(item)

    def release(source):
        return releases[source].version if source in releases else None

    for it in interactions:
        add({
            "kind": "interaction",
            "source": "DGIdb",
            "gene": it["gene"],
            "drug": it["drug"],
            "interaction_types": it["interaction_types"],
            "categories": it["categories"],
            "directionality": it.get("directionality"),
            "score": round(it["score"], 3) if it.get("score") is not None else None,
            "sources": it["sources"],
            "pmids": it["pmids"][:10],
            "drug_approved": it.get("drug_approved"),
            "url": f"https://dgidb.org/genes/{it['gene']}",
        })
    for m in mechanisms:
        add({"kind": "mechanism", "source": "ChEMBL", "release": release("chembl"), **m})
    for g in guidelines:
        add({"kind": "guideline", "source": "CPIC", "categories": ["snp"], "release": release("cpic"), **g})
    for r in cyp_roles:
        add({"kind": "fda_cyp", "source": "FDA DDI table", "release": release("fda_ddi"), "gene": r["gene"],
             "drug": r["drug"], "chembl_id": r["chembl_id"], "pathway": r["pathway"], "system": r["system"],
             "role": r["role"], "strength": r["strength"], "notes": r["notes"], "categories": _role_categories(r),
             "label": _role_label(r), "pmids": [], "url": safety.FDA_TABLE})
    for v in variants:
        add({"kind": "variant_annotation", "source": "PharmGKB", "categories": ["snp"], **v})
    for d in drug_info:
        add({"kind": "drug_info", "source": "DrugBank", **d})
    for p in publications:
        add({"kind": "publication", "source": "PubTator",
             "url": f"https://pubmed.ncbi.nlm.nih.gov/{p['pmid']}/", **p})
    for profile in profiles:
        add(dict(profile))
    return items


def build_network(items, query_entities):
    """Cytoscape.js elements: gene / drug / variant nodes, edges coloured by relation category."""
    query_set = {e.upper() for e in query_entities}
    nodes, edges = {}, {}

    def node(node_id, label, node_type):
        if node_id not in nodes:
            nodes[node_id] = {"data": {"id": node_id, "label": label, "type": node_type,
                                       "is_query": label.upper() in query_set}}

    def edge(source, target, category, evidence_id, label=""):
        key = (source, target, category)
        if key in edges:
            edges[key]["data"]["evidence_ids"].append(evidence_id)
            return
        edges[key] = {"data": {"id": f"e{len(edges) + 1}", "source": source, "target": target,
                               "category": category, "label": label, "evidence_ids": [evidence_id]}}

    def gene_drug(gene, drug, category, evidence_id, label):
        gene, drug = gene.upper(), drug.upper()
        node(f"gene:{gene}", gene, "gene")
        node(f"drug:{drug}", drug, "drug")
        edge(f"gene:{gene}", f"drug:{drug}", category, evidence_id, label)

    for item in items:
        kind = item["kind"]
        if kind == "interaction":
            gene_drug(item["gene"], item["drug"], item["categories"][0], item["id"],
                      ", ".join(item["interaction_types"]))
        elif kind == "mechanism":
            gene_drug(item["gene"], item["drug"], item["categories"][0], item["id"], item["action_type"])
        elif kind == "guideline":
            gene_drug(item["gene"], item["drug"], "snp", item["id"], f"CPIC {item['cpic_level']}")
        elif kind == "fda_cyp":
            gene_drug(item["gene"], item["drug"], item["categories"][0], item["id"], f"{item['label']} (FDA)")
        elif kind == "variant_annotation" and item.get("variant"):
            var_id = f"variant:{item['variant']}"
            node(var_id, item["variant"], "variant")
            for gene in item["genes"]:
                node(f"gene:{gene.upper()}", gene.upper(), "gene")
                edge(f"gene:{gene.upper()}", var_id, "variant_of", item["id"])
            for drug in item["drugs"]:
                node(f"drug:{drug.upper()}", drug.upper(), "drug")
                edge(var_id, f"drug:{drug.upper()}", "snp", item["id"], item.get("level") or "")
    return {"nodes": list(nodes.values()), "edges": list(edges.values())}


def _role_categories(role):
    """FDA role -> relation category: inhibitor -> inhibit, inducer -> induce, CYP substrate -> metabolize."""
    if role["role"] == "inhibitor":
        return ["inhibit"]
    if role["role"] == "inducer":
        return ["induce"]
    return ["metabolize"] if role["system"] == "CYP" else ["other"]


def _role_label(role):
    """'strong CYP2D6 inhibitor', 'sensitive CYP3A substrate', 'P-gp inhibitor'."""
    return " ".join(filter(None, [role.get("strength"), role["pathway"], role["role"]]))
