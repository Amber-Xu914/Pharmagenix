"""Collect evidence from local reference data and live APIs, and normalise it into one bundle.

The bundle is stored on the Query row and drives every output:
  normalisation          -> how each input term was resolved (P53 -> TP53, Plavix -> CLOPIDOGREL)
  items                  -> numbered evidence (E1, E2, ...) the LLM must cite
  table                  -> rows for the structured DGI table
  network                -> Cytoscape.js elements for the relationship graph
  australian_availability-> PBS listing per drug (context only; not DGI evidence, not sent to the LLM)
  source_status          -> which sources succeeded / failed / were skipped, with data versions
"""

import logging

from flask import current_app

from ...reference import lookup
from ...reference.normalise import resolve_entities
from ...reference.text import strip_salt
from ..relations import categorize, matches
from .dgidb import DGIdbClient
from .drugbank import DrugBankClient
from .pharmgkb import PharmGKBClient
from .pubtator import PubTatorClient

logger = logging.getLogger(__name__)

NOT_IMPORTED = "not imported yet; run: flask --app run import-data"


def _safe(status, name, fn, version=None):
    """Run one source; record the outcome instead of letting one failure sink the whole query."""
    try:
        result = fn()
    except Exception as exc:  # noqa: BLE001 - any source failure should be reported, not raised
        logger.warning("Source %s failed: %s", name, exc)
        status[name] = {"state": "error", "error": str(exc)[:300], "count": 0}
        return []
    status[name] = {"state": "ok", "count": len(result)}
    if version:
        status[name]["version"] = version
    return result


def _local(status, releases, name, fn):
    """Like _safe, for a local dataset that may not have been imported yet."""
    release = releases.get(name)
    if release is None:
        status[name] = {"state": "disabled", "count": 0, "error": NOT_IMPORTED}
        return []
    return _safe(status, name, fn, version=release.version)


def collect_evidence(entities, entity_type, relation_types):
    cfg = current_app.config
    timeout = cfg["HTTP_TIMEOUT"]
    status = {}
    releases = lookup.latest_releases()
    wants_snp = not relation_types or "snp" in relation_types

    # 0. Normalise input against HGNC / ChEMBL / PBS names.
    resolved = resolve_entities(entities, entity_type)
    genes = [r["name"] for r in resolved if r["type"] == "gene"]
    drugs = [r["name"] for r in resolved if r["type"] == "drug"]
    drug_ids = [r["id"] for r in resolved if r["type"] == "drug" and r["id"]]
    unresolved = [r["name"] for r in resolved if r["type"] is None]

    # 1. DGIdb: the broad drug–gene interaction list. Unresolved terms are tried as both types.
    dgidb = DGIdbClient(cfg["DGIDB_URL"], timeout)
    interactions = _safe(status, "dgidb", lambda: dgidb.fetch_interactions(
        genes=genes + (unresolved if entity_type in ("gene", "auto") else []),
        drugs=drugs + (unresolved if entity_type in ("drug", "auto") else [])))
    interactions = _filter(_merge_salt_forms(interactions), "interaction_types", relation_types)
    interactions.sort(key=lambda it: it["score"] or 0, reverse=True)
    interactions = interactions[: cfg["MAX_INTERACTIONS"]]
    if status["dgidb"]["state"] == "ok":
        status["dgidb"]["count"] = len(interactions)

    extra_genes, extra_drugs = _classify_unresolved(unresolved, entity_type, interactions)
    query_genes, query_drugs = genes + extra_genes, drugs + extra_drugs

    # 2. ChEMBL (local): curated mechanisms with action types.
    mechanisms = _local(status, releases, "chembl", lambda: lookup.chembl_mechanisms(
        query_genes, drug_ids, limit=cfg["MAX_MECHANISMS"]))
    mechanisms = _filter(mechanisms, "action_type", relation_types)
    if status["chembl"]["state"] == "ok":
        status["chembl"]["count"] = len(mechanisms)

    # 3. SNP-specific response: CPIC guidelines (local) and PharmGKB clinical annotations (live).
    guidelines, variants = [], []
    if wants_snp:
        guidelines = _local(status, releases, "cpic", lambda: lookup.cpic_guidelines(
            query_genes, query_drugs, drug_ids, limit=cfg["MAX_GUIDELINES"]))
        pgkb = PharmGKBClient(cfg["PHARMGKB_URL"], timeout)
        variants = _safe(status, "pharmgkb", lambda: pgkb.clinical_annotations(
            genes=query_genes, drugs=query_drugs, limit=cfg["MAX_VARIANT_ANNOTATIONS"]))
    else:
        status["cpic"] = status["pharmgkb"] = {"state": "skipped", "count": 0}

    # 4. DrugBank: mechanism text for the top drugs (licensed; skipped without a key).
    drugbank = DrugBankClient(cfg["DRUGBANK_URL"], cfg["DRUGBANK_API_KEY"], timeout)
    drug_info = []
    if drugbank.enabled:
        top_drugs = _unique([it["drug"] for it in interactions])[:10] or query_drugs
        drug_info = _safe(status, "drugbank", lambda: drugbank.lookup_drugs(top_drugs))
    else:
        status["drugbank"] = {"state": "disabled", "count": 0, "error": "DRUGBANK_API_KEY not set"}

    # 5. PubTator: literature for the strongest pairs.
    pubtator = PubTatorClient(cfg["PUBTATOR_URL"], timeout)
    pairs = [(it["gene"], it["drug"]) for it in interactions] or [(m["gene"], m["drug"]) for m in mechanisms]
    publications = _safe(status, "pubtator", lambda: pubtator.search_pairs(
        pairs, query_genes + query_drugs, limit_total=cfg["MAX_PUBLICATIONS"]))

    # 6. PBS (local): Australian listing for the queried drugs and the most relevant related drugs.
    pbs_drugs = _pbs_candidates(resolved, query_drugs, guidelines, mechanisms, interactions, cfg["MAX_PBS_DRUGS"])
    availability = _local(status, releases, "pbs", lambda: lookup.pbs_availability(pbs_drugs))
    if status["pbs"]["state"] == "ok":
        status["pbs"]["count"] = sum(1 for a in availability if a["pbs_listed"])
    elif status["pbs"]["state"] == "disabled":
        availability = None

    items = build_evidence_items(interactions, variants, drug_info, publications, mechanisms, guidelines,
                                 releases)
    return {
        "normalisation": resolved,
        "items": items,
        "table": build_table(items),
        "network": build_network(items, query_genes + query_drugs),
        "australian_availability": availability,
        "source_status": status,
        "counts": {
            "interactions": len(interactions),
            "mechanisms": len(mechanisms),
            "guidelines": len(guidelines),
            "variant_annotations": len(variants),
            "publications": len(publications),
            "drug_info": len(drug_info),
        },
    }


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


def _classify_unresolved(unresolved, entity_type, interactions):
    """Terms missing from the reference data: decide gene vs drug from what DGIdb returned."""
    if entity_type == "gene":
        return unresolved, []
    if entity_type == "drug":
        return [], unresolved
    genes_seen = {it["gene"] for it in interactions}
    drugs_seen = {it["drug"] for it in interactions}
    drugs = [e for e in unresolved if e in drugs_seen and e not in genes_seen]
    return [e for e in unresolved if e not in drugs], drugs  # anything still unknown is tried as a gene


def _pbs_candidates(resolved, query_drugs, guidelines, mechanisms, interactions, limit):
    ids = {r["name"]: r["id"] for r in resolved if r["type"] == "drug"}
    candidates = (
        query_drugs
        + [g["drug"].upper() for g in guidelines]
        + [m["drug"] for m in mechanisms if (m.get("max_phase") or 0) >= 4]
        + [it["drug"] for it in interactions if it.get("drug_approved")]
    )
    names, seen = [], set()
    for name in candidates:
        if name and strip_salt(name) not in seen:
            seen.add(strip_salt(name))
            names.append(name)
    names = names[:limit]
    chembl_ids = {g["drug"].upper(): g.get("chembl_id") for g in guidelines}
    chembl_ids.update({m["drug"]: m["chembl_id"] for m in mechanisms})
    return [(name, ids.get(name) or chembl_ids.get(name)) for name in names]


def build_evidence_items(interactions, variants, drug_info, publications, mechanisms=(), guidelines=(),
                         releases=None):
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
    for v in variants:
        add({"kind": "variant_annotation", "source": "PharmGKB", "categories": ["snp"], **v})
    for d in drug_info:
        add({"kind": "drug_info", "source": "DrugBank", **d})
    for p in publications:
        add({"kind": "publication", "source": "PubTator",
             "url": f"https://pubmed.ncbi.nlm.nih.gov/{p['pmid']}/", **p})
    return items


def build_table(items):
    rows = []
    for item in items:
        kind = item["kind"]
        if kind == "interaction":
            rows.append(_row(item, relation=", ".join(item["interaction_types"]) or "unspecified",
                             category=item["categories"][0], score=item["score"], sources=item["sources"]))
        elif kind == "mechanism":
            rows.append(_row(item, relation=f"{item['action_type'] or 'unspecified'}: {item['mechanism']}",
                             category=item["categories"][0], sources=["ChEMBL"],
                             level=f"Phase {item['max_phase']:g}" if item.get("max_phase") else None))
        elif kind == "guideline":
            rows.append(_row(item, relation=f"CPIC guideline: {item.get('guideline_name') or 'no guideline yet'}",
                             category="snp", level=f"CPIC {item['cpic_level']}", sources=["CPIC"],
                             variant="genotype-guided"))
        elif kind == "variant_annotation":
            rows.append(_row(item, gene=", ".join(item["genes"]), drug=", ".join(item["drugs"]),
                             variant=item["variant"], category="snp",
                             relation=", ".join(item["phenotype_categories"]) or "clinical annotation",
                             score=item.get("score"), level=item["level"], sources=["PharmGKB"]))
    return rows


def _row(item, **fields):
    row = {
        "evidence_id": item["id"],
        "gene": item.get("gene"),
        "drug": item.get("drug"),
        "variant": None,
        "score": None,
        "level": None,
        "pmids": item.get("pmids") or [],
        "url": item.get("url"),
    }
    row.update(fields)
    return row


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
