"""ChEMBL curated drug mechanisms, their human gene targets, and drug synonyms (CC BY-SA 3.0).

Only the mechanism-of-action table (~7,500 records) is imported, not the full 35 GB database:
it carries the action types (inhibitor, agonist, ...) that most DGIdb interactions lack.
"""

from concurrent.futures import ThreadPoolExecutor

from flask import current_app

from ..models import Drug, DrugMechanism, DrugSynonym
from ..text import norm
from . import gene_symbol_index, http_session, replace_tables

SET_BATCH = 50
TARGET_FIELDS = "target_chembl_id,pref_name,target_type,organism,target_components"
MOLECULE_FIELDS = ("molecule_chembl_id,pref_name,max_phase,molecule_type,first_approval,molecule_synonyms,"
                   "molecule_hierarchy")


def run(echo):
    base = current_app.config["CHEMBL_API_URL"].rstrip("/")
    session = http_session()
    version = session.get(f"{base}/status.json", timeout=60).json().get("chembl_db_version")

    echo(f"[chembl] {version}: downloading mechanisms")
    mechanisms = _paginate(session, f"{base}/mechanism.json", "mechanisms")
    target_ids = sorted({m["target_chembl_id"] for m in mechanisms if m.get("target_chembl_id")})
    molecule_ids = sorted({m.get("parent_molecule_chembl_id") or m["molecule_chembl_id"] for m in mechanisms})

    echo(f"[chembl] {len(mechanisms)} mechanisms; fetching {len(target_ids)} targets")
    targets = _fetch_sets(session, base, "target", target_ids, TARGET_FIELDS, "targets", "target_chembl_id")
    echo(f"[chembl] fetching {len(molecule_ids)} drugs and their salt forms (this takes a few minutes)")
    molecules = _fetch_with_salts(session, base, molecule_ids, echo)

    drugs, synonyms = build_drugs(molecules, set(molecule_ids))
    drug_names = {d["chembl_id"]: d["pref_name"] for d in drugs}
    rows, skipped = build_mechanisms(mechanisms, targets, drug_names, gene_symbol_index())
    echo(f"[chembl] writing {len(drugs)} drugs, {len(synonyms)} synonyms, {len(rows)} mechanism rows")
    replace_tables([(Drug, drugs), (DrugSynonym, synonyms), (DrugMechanism, rows)])
    return {
        "version": version,
        "url": base,
        "counts": {"drugs": len(drugs), "drug_synonyms": len(synonyms), "drug_mechanisms": len(rows)},
        "notes": f"{skipped} mechanisms skipped (non-human or no gene-level target)",
    }


def _paginate(session, url, key, limit=1000, extra=None):
    out, offset = [], 0
    while True:
        page = session.get(url, params={"limit": limit, "offset": offset, **(extra or {})}, timeout=180).json()
        out += page[key]
        offset += limit
        if not page["page_meta"].get("next"):
            return out


def _fetch_sets(session, base, resource, ids, fields, key, id_field, echo=None):
    batches = [ids[i:i + SET_BATCH] for i in range(0, len(ids), SET_BATCH)]

    def fetch(batch):
        url = f"{base}/{resource}/set/{';'.join(batch)}.json"
        return session.get(url, params={"only": fields}, timeout=180).json().get(key, [])

    results = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for i, records in enumerate(pool.map(fetch, batches), 1):
            results.update({r[id_field]: r for r in records})
            if echo and i % 20 == 0:
                echo(f"[chembl]   {i}/{len(batches)} batches")
    return results


def _fetch_with_salts(session, base, parent_ids, echo):
    """Parent molecules plus every salt form of them. Trade names usually sit on the salt (Plavix is
    clopidogrel bisulfate), so salts are fetched only to attach their names to the parent drug."""
    batches = [parent_ids[i:i + SET_BATCH] for i in range(0, len(parent_ids), SET_BATCH)]

    def fetch(batch):
        return _paginate(session, f"{base}/molecule.json", "molecules", extra={
            "molecule_hierarchy__parent_chembl_id__in": ",".join(batch), "only": MOLECULE_FIELDS})

    molecules = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for i, records in enumerate(pool.map(fetch, batches), 1):
            molecules += records
            if i % 20 == 0:
                echo(f"[chembl]   {i}/{len(batches)} batches")

    # A few molecules (mostly biologics) have no hierarchy record, so the filter above misses them.
    found = {m["molecule_chembl_id"] for m in molecules}
    missing = [m for m in parent_ids if m not in found]
    if missing:
        echo(f"[chembl] fetching {len(missing)} molecules without a hierarchy record")
        molecules += _fetch_sets(session, base, "molecule", missing, MOLECULE_FIELDS, "molecules",
                                 "molecule_chembl_id").values()
    return molecules


def _parent_id(mol):
    return (mol.get("molecule_hierarchy") or {}).get("parent_chembl_id") or mol["molecule_chembl_id"]


def build_drugs(molecules, parent_ids):
    molecules = sorted(molecules, key=lambda m: m["molecule_chembl_id"] != _parent_id(m))  # parents first
    drugs, synonyms, seen = {}, [], set()
    for mol in molecules:
        chembl_id = _parent_id(mol)
        if chembl_id not in parent_ids:
            continue
        is_parent = mol["molecule_chembl_id"] == chembl_id
        if is_parent:
            drugs[chembl_id] = {
                "chembl_id": chembl_id,
                "pref_name": (mol.get("pref_name") or chembl_id)[:255],
                "molecule_type": mol.get("molecule_type"),
                "max_phase": float(mol["max_phase"]) if mol.get("max_phase") not in (None, "") else None,
                "first_approval": mol.get("first_approval"),
            }
        names = [(mol.get("pref_name"), "PREF_NAME" if is_parent else "SALT_NAME"),
                 (mol["molecule_chembl_id"], "CHEMBL_ID")]
        names += [(s.get("molecule_synonym"), s.get("syn_type") or "OTHER") for s in mol.get("molecule_synonyms") or []]
        for name, syn_type in names:
            if not name or len(name) > 255 or (chembl_id, norm(name)) in seen:
                continue
            seen.add((chembl_id, norm(name)))
            synonyms.append({"chembl_id": chembl_id, "synonym": name, "synonym_norm": norm(name),
                             "syn_type": syn_type[:32]})
    synonyms = [s for s in synonyms if s["chembl_id"] in drugs]
    return list(drugs.values()), synonyms


def build_mechanisms(mechanisms, targets, drug_names, gene_index):
    rows, skipped = {}, 0
    for mec in mechanisms:
        target = targets.get(mec.get("target_chembl_id"))
        if not target or target.get("organism") != "Homo sapiens":
            skipped += 1
            continue
        symbols = []
        for component in target.get("target_components") or []:
            for syn in component.get("target_component_synonyms") or []:
                symbol = norm(syn.get("component_synonym"))
                if syn.get("syn_type") == "GENE_SYMBOL" and symbol not in symbols:
                    symbols.append(symbol)
        if not symbols:
            skipped += 1
            continue
        drug_id = mec.get("parent_molecule_chembl_id") or mec["molecule_chembl_id"]
        refs = mec.get("mechanism_refs") or []
        pmids = [int(r["ref_id"]) for r in refs if r.get("ref_type") == "PubMed" and str(r.get("ref_id")).isdigit()]
        urls = [r["ref_url"] for r in refs if r.get("ref_url")]
        for symbol in symbols:
            key = (drug_id, target["target_chembl_id"], mec.get("action_type"), symbol)
            if key in rows:  # same mechanism recorded for another salt form: keep one row, merge references
                row = rows[key]
                row["pmids"] = list(dict.fromkeys(row["pmids"] + pmids))
                row["ref_urls"] = list(dict.fromkeys(row["ref_urls"] + urls))[:5]
                continue
            rows[key] = {
                "mec_id": mec["mec_id"],
                "chembl_id": drug_id,
                "drug_name": drug_names.get(drug_id),
                "target_chembl_id": target["target_chembl_id"],
                "target_name": (target.get("pref_name") or "")[:255],
                "target_type": target.get("target_type"),
                "hgnc_id": gene_index.get(symbol),
                "gene_symbol": symbol,
                "action_type": mec.get("action_type"),
                "mechanism_of_action": (mec.get("mechanism_of_action") or "")[:500],
                "direct_interaction": bool(mec.get("direct_interaction")),
                "disease_efficacy": bool(mec.get("disease_efficacy")),
                "max_phase": float(mec["max_phase"]) if mec.get("max_phase") is not None else None,
                "pmids": pmids,
                "ref_urls": urls[:5],
            }
    return list(rows.values()), skipped
