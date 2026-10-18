"""Stage 4, Evidence: integrate and validate the retrieved records with fixed rules (no interpretation here).

  - merge every record about the same gene–drug pair (DGIdb, ChEMBL, CPIC, PharmGKB) into one row
  - standardise interaction types into the relation categories (relations.py)
  - link each row to its evidence IDs and PMIDs
  - grade the evidence and flag conflicting reports
  - decide "interactions found?": when nothing was found the LLM is not called
"""

from ...reference.text import strip_salt
from ..relations import categorize

DGI_KINDS = ("interaction", "mechanism", "guideline", "variant_annotation")
CATEGORY_ORDER = ["inhibit", "induce", "bind", "metabolize", "snp", "other"]

GRADES = {
    "strong": "Strong",          # actionable clinical evidence: CPIC A/B, PharmGKB 1A/1B
    "moderate": "Moderate",      # PharmGKB 2A/2B, curated ChEMBL mechanism, or broad DGIdb support with literature
    "supportive": "Supportive",  # weaker clinical levels, or DGIdb support from 2+ databases or literature
    "limited": "Limited",        # a single database record without literature
}
GRADE_ORDER = list(GRADES)

CPIC_STRONG = {"A", "A/B", "B"}
CPIC_ORDER = ["A", "A/B", "B", "B/C", "C", "C/D", "D"]
PGKB_ORDER = ["1A", "1B", "2A", "2B", "3", "4"]


def pair_key(gene, drug):
    return (str(gene or "").upper(), strip_salt(drug or ""))


def item_pairs(item):
    """The (gene, drug) pairs an evidence item is about."""
    if item["kind"] in ("interaction", "mechanism", "guideline"):
        return [pair_key(item["gene"], item["drug"])]
    if item["kind"] == "variant_annotation":
        return [pair_key(g, d) for g in item["genes"] for d in item["drugs"]]
    return []


def consolidate_pairs(items):
    """One row per gene–drug pair, graded, strongest first."""
    rows = {}
    for item in items:
        if item["kind"] not in DGI_KINDS:
            continue
        for key in item_pairs(item):
            row = rows.setdefault(key, _new_row(key))
            _add(row, item)
    out = [_finish(row) for row in rows.values()]
    out.sort(key=lambda r: (GRADE_ORDER.index(r["grade"]), r["category"] == "other", -(r["score"] or 0)))
    return out


def _new_row(key):
    return {"gene": key[0], "drug": key[1], "evidence_ids": [], "categories": [], "actions": [], "mechanism": None,
            "sources": [], "databases": [], "score": None, "max_phase": None, "drug_approved": None,
            "cpic_level": None, "pgkb_level": None, "pmids": [], "url": None}


def _add(row, item):
    kind = item["kind"]
    row["evidence_ids"].append(item["id"])
    row["pmids"] += [p for p in item.get("pmids") or [] if p not in row["pmids"]]
    row["sources"] = sorted(set(row["sources"]) | {item["source"]})
    for category in item.get("categories") or []:
        if category not in row["categories"]:
            row["categories"].append(category)
    if kind == "interaction":
        row["actions"] += [{"label": t, "source": "DGIdb"} for t in item["interaction_types"]]
        row["databases"] = sorted(set(row["databases"]) | set(item["sources"]))
        row["score"] = max(row["score"] or 0, item["score"] or 0)
        row["drug_approved"] = row["drug_approved"] or item.get("drug_approved")
        row["url"] = row["url"] or item.get("url")
    elif kind == "mechanism":
        if item.get("action_type"):
            row["actions"].append({"label": item["action_type"], "source": "ChEMBL"})
        row["mechanism"] = row["mechanism"] or item.get("mechanism")
        row["max_phase"] = max(row["max_phase"] or 0, item.get("max_phase") or 0)
        row["url"] = item.get("url") or row["url"]  # the curated record is the better link
    elif kind == "guideline":
        row["cpic_level"] = _best(row["cpic_level"], item.get("cpic_level"), CPIC_ORDER)
    elif kind == "variant_annotation":
        row["pgkb_level"] = _best(row["pgkb_level"], item.get("level"), PGKB_ORDER)


def _best(current, new, order):
    ranked = [v for v in (current, new) if v in order]
    return min(ranked, key=order.index) if ranked else current or new


def _finish(row):
    typed = [c for c in row["categories"] if c != "other"]
    row["categories"] = sorted(typed or ["other"], key=CATEGORY_ORDER.index)
    row["category"] = row["categories"][0]
    row["evidence_id"] = row["evidence_ids"][0]
    row["pmids"] = row["pmids"][:30]
    labels = list(dict.fromkeys(a["label"] for a in row["actions"] if a["label"]))
    row["relation"] = row["mechanism"] or ", ".join(labels) or (
        "pharmacogenomic association" if row["category"] == "snp" else "unspecified")
    row["level"] = " · ".join(filter(None, [
        row["cpic_level"] and f"CPIC {row['cpic_level']}",
        row["pgkb_level"] and f"PharmGKB {row['pgkb_level']}",
        row["max_phase"] and f"Phase {row['max_phase']:g}",
    ])) or None
    row["grade"], row["grade_reason"] = grade(row)
    row["grade_label"] = GRADES[row["grade"]]
    row["conflicts"] = conflicts(row)
    return row


def grade(row):
    """(grade, reason) from the strongest evidence behind the pair."""
    cpic, pgkb = row["cpic_level"], row["pgkb_level"]
    n_db, has_pmids = len(row["databases"]), bool(row["pmids"])
    if cpic in CPIC_STRONG:
        return "strong", f"CPIC level {cpic} prescribing guideline"
    if pgkb in ("1A", "1B"):
        return "strong", f"PharmGKB level {pgkb} clinical annotation"
    if pgkb in ("2A", "2B"):
        return "moderate", f"PharmGKB level {pgkb} clinical annotation"
    if "ChEMBL" in row["sources"]:
        approved = " (approved drug)" if (row["max_phase"] or 0) >= 4 else ""
        return "moderate", f"curated ChEMBL mechanism of action{approved}"
    if n_db >= 3 and has_pmids:
        return "moderate", f"reported by {n_db} databases with literature support (DGIdb)"
    if cpic or pgkb == "3":
        return "supportive", f"lower-level pharmacogenomic evidence ({row['level']})"
    if n_db >= 2 or has_pmids:
        detail = f"{n_db} databases" if n_db >= 2 else "1 database"
        return "supportive", f"{detail}{' with literature' if has_pmids else ''} (DGIdb)"
    return "limited", "single database record without literature"


def conflicts(row):
    """Opposite directions of action reported for the same pair (can also mean mixed agonist/antagonist)."""
    by_direction = {"inhibit": [], "induce": []}
    for action in row["actions"]:
        for category in categorize([action["label"]]):
            if category in by_direction:
                by_direction[category].append(f"{action['label']} ({action['source']})")
    if by_direction["inhibit"] and by_direction["induce"]:
        return [f"Opposite actions reported: {', '.join(dict.fromkeys(by_direction['inhibit']))} vs "
                f"{', '.join(dict.fromkeys(by_direction['induce']))}. This may reflect mixed activity or different "
                "assays; check the sources."]
    return []


def decide_outcome(items, status):
    """The "Interactions found?" decision, with the sources checked so the page can say what was searched."""
    found = any(i["kind"] in DGI_KINDS for i in items)
    # PBS is Australian listing context, not a drug–gene interaction source.
    checked = [name for name, s in status.items() if s.get("state") == "ok" and name != "pbs"]
    unavailable = [name for name, s in status.items() if s.get("state") == "error" and name != "pbs"]
    return {"status": "found" if found else "none", "checked_sources": checked, "unavailable_sources": unavailable}
