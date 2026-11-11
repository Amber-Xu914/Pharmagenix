"""Drug safety checks before a drug is shown (deterministic rules; the LLM only reports what they found).

Two checks:

  Warnings   ChEMBL drug warnings: a withdrawn drug (with the year, countries and toxicity class) or a boxed
             ("black box") warning.
  Drug–drug  Each drug of the result against the patient's current medications, and the drugs inside each combination
             regimen against each other. For a drug A whose exposure or effect may change (the "affected" drug) and
             another drug B (the "perpetrator"), four rules, strongest source first:

    label class      A's FDA label names a class (e.g. "Avoid ... strong CYP3A inducers") and the FDA table puts B
                     in that class (carbamazepine: strong CYP3A inducer)
    label names drug A's FDA label names B itself (erlotinib: "... ketoconazole ...")
    FDA table        both drugs are in the FDA table: A a (moderately) sensitive substrate of a pathway, B a strong or
                     moderate inhibitor / inducer of it (or a transporter substrate and inhibitor)
    phenoconversion  drug–drug–gene: B strongly or moderately inhibits a pharmacogene (CYP2C19, CYP2D6, ...) for which
                     A has a CPIC guideline, so the patient's functional phenotype may differ from the genotype

Severity: "major" when the label sentence says to avoid the combination (or a strong inhibitor / inducer meets a
sensitive substrate, or a strong inhibitor causes phenoconversion); "moderate" otherwise; "minor" for weak
modulators. Statements in a label that say no adjustment is needed are not flagged.
"""

import logging
import re

from flask import current_app

from ..reference import lookup
from ..reference.importers import drug_synonym_index, lookup_drug
from ..reference.text import norm, strip_salt
from .biomed.openfda import OpenFDAClient

logger = logging.getLogger(__name__)

FDA_TABLE = ("https://www.fda.gov/drugs/drug-interactions-labeling/"
             "healthcare-professionals-fdas-examples-drugs-interact-cyp-enzymes-and-transporter-systems")
SEVERITY_ORDER = ["major", "moderate", "minor"]
PHARMACOGENES = {"CYP2C19", "CYP2D6", "CYP2C9", "CYP2B6", "CYP1A2"}
AVOID = re.compile(r"\b(avoid|contraindicat|do not (?:co-?administer|use|take)|should not be (?:co-?administered|used))",
                   re.I)
NO_EFFECT = re.compile(r"no (?:dose |dosage )?adjustments? (?:is |are )?(?:required|needed|necessary)|"
                       r"no clinically (?:meaningful|significant|relevant)|did not (?:affect|change|alter)", re.I)
STRENGTH = r"(?:strong|moderate|weak)"
CLASS = re.compile(
    rf"(?P<strength>{STRENGTH}(?:\s*(?:,|/|and/or|or|and|to)\s*{STRENGTH})*)?\s*(?:or\s+|and\s+)?"
    rf"(?:combined\s+)?(?P<pathways>(?:CYP\s?\d[A-Z]\d{{0,2}}(?:\s*(?:,|/|and/or|or|and)\s*)?)+|P-gp|P-glycoprotein|BCRP|"
    rf"OATP1B1|OATP1B3|OATP1B)\s+(?P<role>inhibitor|inducer)s?", re.I)
TRANSPORTER_NAMES = {"P-GLYCOPROTEIN": "P-gp", "P-GP": "P-gp", "BCRP": "BCRP", "OATP1B1": "OATP1B1",
                     "OATP1B3": "OATP1B3", "OATP1B": "OATP1B"}


# ---------- inputs ----------

def parse_medications(raw):
    """A list or a comma / semicolon / newline-separated string -> clean names (at most MAX_CURRENT_MEDICATIONS)."""
    if not raw:
        return []
    parts = raw if isinstance(raw, list) else re.split(r"[,;\n]+", str(raw))
    names = [re.sub(r"\s+", " ", str(p)).strip()[:100] for p in parts]
    return list(dict.fromkeys(n for n in names if n))[: current_app.config["MAX_CURRENT_MEDICATIONS"]]


def resolve_drug(name, index=None, keep_name=False):
    """{"input", "name", "chembl_id"}; an unknown name keeps its text (the label search still uses it).
    keep_name: show the name as given (a regimen's "Docetaxel", not ChEMBL's "DOCETAXEL ANHYDROUS"); otherwise a
    brand or misspelt-case name is shown by its preferred name (Tegretol -> Carbamazepine)."""
    index = drug_synonym_index() if index is None else index
    chembl_id = lookup_drug(index, name) if index else None
    pref = None
    if chembl_id:
        from ..reference.models import Drug
        from ..extensions import db
        drug = db.session.get(Drug, chembl_id)
        pref = drug.pref_name if drug else None
    display = (name if keep_name or not pref else pref).strip()
    return {"input": name, "name": display.title() if display.isupper() else display, "chembl_id": chembl_id}


# ---------- warnings ----------

def warnings_for(drugs):
    """drugs: [{"name", "chembl_id"}] -> {chembl_id: [warning]}."""
    return lookup.drug_warnings([d.get("chembl_id") for d in drugs])


def warning_summary(warnings):
    """One line per warning, e.g. 'Withdrawn (United States, 2005): cardiotoxicity'."""
    out = []
    for w in warnings or []:
        if w["type"] == "Withdrawn":
            where = ", ".join(filter(None, [w.get("country"), str(w["year"]) if w.get("year") else None]))
            out.append(f"Withdrawn{f' ({where})' if where else ''}{': ' + w['class'] if w.get('class') else ''}")
        else:
            out.append(f"Boxed warning{': ' + w['class'] if w.get('class') else ''}")
    return list(dict.fromkeys(out))


# ---------- drug–drug interactions ----------

class Checker:
    """Caches labels, FDA roles and names per drug for one result."""

    def __init__(self, status):
        cfg = current_app.config
        self.client = OpenFDAClient(cfg["OPENFDA_URL"], cfg["OPENFDA_API_KEY"], cfg["HTTP_TIMEOUT"])
        self.status = status
        self._labels, self._roles, self._names, self._cpic = {}, {}, {}, {}
        self.label_errors = 0

    def label(self, drug):
        key = strip_salt(drug["name"])
        if key not in self._labels:
            try:
                self._labels[key] = self.client.label(drug["name"].split(" (")[0])
            except Exception as exc:  # noqa: BLE001 - a missing label weakens the check, it does not stop it
                logger.warning("openFDA label for %s failed: %s", drug["name"], exc)
                self.label_errors += 1
                self._labels[key] = None
        return self._labels[key]

    def roles(self, drug):
        key = drug.get("chembl_id") or strip_salt(drug["name"])
        if key not in self._roles:
            self._roles[key] = lookup.cyp_roles(drug.get("chembl_id"), drug["name"])
        return self._roles[key]

    def names(self, drug):
        key = drug.get("chembl_id") or strip_salt(drug["name"])
        if key not in self._names:
            names = {drug["name"], *lookup.official_names(drug.get("chembl_id"))}
            self._names[key] = sorted({n for n in names if n and len(n) >= 5}, key=len, reverse=True)
        return self._names[key]

    def cpic(self, drug):
        key = drug.get("chembl_id") or strip_salt(drug["name"])
        if key not in self._cpic:
            self._cpic[key] = lookup.cpic_genes(drug.get("chembl_id"), drug["name"])
        return self._cpic[key]

    def pair(self, affected, other, context):
        """Alerts for `other` changing the exposure or effect of `affected`."""
        alerts, covered = [], set()
        label = self.label(affected)
        other_roles = self.roles(other)
        if label:
            sentences = _sentences(label["text"])
            for i, sentence in enumerate(sentences):
                if NO_EFFECT.search(sentence):
                    continue
                for m in CLASS.finditer(sentence):
                    role = m.group("role").lower()
                    strengths = set(re.findall(STRENGTH, m.group("strength") or "", re.I)) or {"strong", "moderate"}
                    strengths = {s.lower() for s in strengths}
                    for pathway in _pathways(m.group("pathways")):
                        hit = next((r for r in other_roles if r["pathway"] == pathway and r["role"] == role
                                    and (r["strength"] in strengths or r["system"] == "transporter")), None)
                        if not hit or (pathway, role) in covered:
                            continue
                        covered.add((pathway, role))
                        severity = ("major" if _advises_avoiding(sentences, i, pathway, role)
                                    else "minor" if hit["strength"] == "weak" else "moderate")
                        alerts.append(_alert(affected, other, context, severity, "label class",
                                             f"{_strength(hit)}{pathway} {role}", pathway, label, sentence, hit))
            if not alerts:
                for name in self.names(other):
                    if norm(name) == norm(affected["name"]):
                        continue
                    sentence = next((s for s in _sentences(label["text"])
                                     if re.search(rf"\b{re.escape(name)}\b", s, re.I) and not NO_EFFECT.search(s)), None)
                    if sentence:
                        alerts.append(_alert(affected, other, context, "major" if AVOID.search(sentence) else "moderate",
                                             "label names drug", f"{other['name']} named in the {affected['name']} label",
                                             None, label, sentence, None))
                        break
        # FDA table on both sides, for pathways the label did not cover
        for sub in self.roles(affected):
            if sub["role"] != "substrate":
                continue
            for mod in other_roles:
                if mod["pathway"] != sub["pathway"] or mod["role"] not in ("inhibitor", "inducer"):
                    continue
                if (sub["pathway"], mod["role"]) in covered or (mod["system"] == "CYP" and mod["strength"] == "weak"):
                    continue
                covered.add((sub["pathway"], mod["role"]))
                severity = ("major" if mod["strength"] == "strong" and sub["strength"] == "sensitive" else "moderate")
                alerts.append(_alert(affected, other, context, severity, "FDA table",
                                     f"{_strength(mod)}{mod['pathway']} {mod['role']} + {sub['strength'] or ''} "
                                     f"{sub['pathway']} substrate".replace("  ", " "),
                                     sub["pathway"], None, None, mod, substrate=sub))
        # drug–drug–gene: phenoconversion of a pharmacogene the affected drug has a CPIC guideline for
        guidelines = self.cpic(affected)
        for mod in other_roles:
            gene = mod["gene"]
            if (mod["role"] == "inhibitor" and mod["strength"] in ("strong", "moderate") and gene in PHARMACOGENES
                    and gene in guidelines):
                phenotype = "poor" if mod["strength"] == "strong" else "intermediate"
                alerts.append(_alert(
                    affected, other, context, "major" if mod["strength"] == "strong" else "moderate", "phenoconversion",
                    f"{gene} phenoconversion ({mod['strength']} inhibitor)", mod["pathway"], None, None, mod,
                    cpic={"gene": gene, "level": guidelines[gene], "phenotype": phenotype}))
        return alerts


def _sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.;\]])\s+(?=[A-Z0-9(•])|\s+•\s+", text or "") if len(s.strip()) > 15]


def _advises_avoiding(sentences, i, pathway, role):
    """True when the sentence, or one of the next three about the same pathway and role, says to avoid the
    combination ("Avoid co-administering erlotinib with strong CYP3A4 inhibitors" follows the effect sentence)."""
    token = pathway.replace("CYP3A", "CYP3A").lower()
    for j, s in enumerate(sentences[i:i + 4]):
        lower = s.lower().replace(" ", "")
        about = j == 0 or (token.replace(" ", "") in lower and role in lower)
        if about and AVOID.search(s) and not NO_EFFECT.search(s):
            return True
    return False


def _pathways(text):
    out = []
    for token in re.findall(r"CYP\s?\d[A-Z]\d{0,2}|P-gp|P-glycoprotein|BCRP|OATP1B[13]?", text, re.I):
        token = token.upper().replace(" ", "")
        if token.startswith("CYP"):
            out.append("CYP3A" if token.startswith("CYP3A") else token)
        else:
            out.append(TRANSPORTER_NAMES.get(token, token))
    return list(dict.fromkeys(out))


def _strength(role):
    return f"{role['strength']} " if role.get("strength") else ""


def _alert(affected, other, context, severity, rule, mechanism, pathway, label, sentence, role, substrate=None,
           cpic=None):
    if rule == "phenoconversion":
        summary = (f"{other['name']} is a {role['strength']} {cpic['gene']} inhibitor (FDA): a patient taking it can "
                   f"respond like a {cpic['gene']} {cpic['phenotype']} metaboliser whatever the genotype, so the CPIC "
                   f"{cpic['gene']}–{affected['name']} guideline (level {cpic['level']}) applies to the functional "
                   f"phenotype.")
    elif rule == "FDA table":
        effect = "raise" if role["role"] == "inhibitor" else "lower"
        summary = (f"{other['name']} ({_strength(role)}{role['pathway']} {role['role']}, FDA) may {effect} the "
                   f"exposure of {affected['name']} ({substrate['strength'] or ''} {substrate['pathway']} substrate, "
                   f"FDA).").replace("( ", "(")
    elif rule == "label class":
        summary = (f"The {affected['name']} label covers {_strength(role)}{role['pathway']} {role['role']}s, and "
                   f"{other['name']} is one (FDA).")
    else:
        summary = f"The {affected['name']} label names {other['name']} in its drug interactions section."
    return {
        "drug": affected["name"], "chembl_id": affected.get("chembl_id"),
        "other": other["name"], "other_chembl_id": other.get("chembl_id"),
        "context": context, "severity": severity, "rule": rule, "mechanism": mechanism[:255], "pathway": pathway,
        "summary": summary,
        "label_text": (sentence[:500] + ("…" if len(sentence) > 500 else "")) if sentence else None,
        "label": {k: label[k] for k in ("title", "set_id", "effective_time", "url")} if label else None,
        "fda_role": (f"{role['drug'] if 'drug' in role else other['name']}: {_strength(role)}{role['pathway']} "
                     f"{role['role']}" if role else None),
        "fda_notes": role.get("notes") if role else None,
        "cpic": cpic,
        "url": label["url"] if label else FDA_TABLE,
    }


def check_interactions(drugs, medications, combinations, status):
    """drugs: [{"name", "chembl_id"}] of the result, checked against medications (same shape).
    combinations: [[component, ...]] regimens whose components are checked against each other.
    Returns the alerts, most severe first; status["openfda"] records the label lookups."""
    if not medications and not combinations:
        status["openfda"] = {"state": "skipped", "count": 0}
        return []
    checker = Checker(status)
    alerts = []
    for drug in drugs:
        for med in medications:
            if strip_salt(drug["name"]) == strip_salt(med["name"]) or (
                    drug.get("chembl_id") and drug.get("chembl_id") == med.get("chembl_id")):
                continue
            alerts += checker.pair(drug, med, "current medication")
            alerts += checker.pair(med, drug, "current medication")
    for components in combinations:
        for a in components:
            for b in components:
                if a is not b and strip_salt(a["name"]) != strip_salt(b["name"]):
                    alerts += checker.pair(a, b, "combination regimen")
    # One alert per pair, rule and pathway. A label naming the other drug says nothing about the direction, so the
    # pair's two labels count once (the more severe one is kept).
    alerts.sort(key=lambda a: SEVERITY_ORDER.index(a["severity"]))
    seen, unique = set(), []
    for a in alerts:
        pair = (strip_salt(a["drug"]), strip_salt(a["other"]))
        if a["rule"] == "label names drug":
            pair = tuple(sorted(pair))
        key = (*pair, a["rule"], a["pathway"], a["context"])
        if key not in seen:
            seen.add(key)
            unique.append(a)
    unique.sort(key=lambda a: (SEVERITY_ORDER.index(a["severity"]), a["context"], a["drug"], a["other"]))
    looked_up = len(checker._labels)
    status["openfda"] = ({"state": "error", "count": 0, "error": "every label lookup failed"}
                         if looked_up and checker.label_errors == looked_up
                         else {"state": "ok", "count": sum(1 for v in checker._labels.values() if v)})
    return unique


def evidence_items(alerts, start):
    """The alerts as evidence items (kind "ddi") numbered from E{start}; sets each alert's evidence_id."""
    items = []
    for i, a in enumerate(alerts, start=start):
        a["evidence_id"] = f"E{i}"
        source = {"label class": "FDA label + FDA DDI table", "label names drug": "FDA label",
                  "FDA table": "FDA DDI table", "phenoconversion": "FDA DDI table + CPIC"}[a["rule"]]
        items.append({"id": a["evidence_id"], "kind": "ddi", "source": source, **{k: a[k] for k in (
            "drug", "other", "context", "severity", "rule", "mechanism", "summary", "label_text", "fda_role", "url")},
            "label_title": (a["label"] or {}).get("title"), "label_date": (a["label"] or {}).get("effective_time")})
    return items


def worst(alerts):
    return min((a["severity"] for a in alerts), key=SEVERITY_ORDER.index, default=None)


# ---------- Workflow 4: stage 7 safety check on the ranked tracks ----------

def components(drug_name, index):
    """'Amivantamab + Carboplatin + Pemetrexed' -> one resolved entry per drug of the regimen."""
    return [resolve_drug(part.strip(), index, keep_name=True) for part in drug_name.split(" + ") if part.strip()]


def check_candidates(tracks, medications, items, status):
    """Adds c["safety"] (warnings, interactions, penalty points) to every ranked candidate, re-ranks each track with
    the safety points included, and returns the evidence["safety"] block. New evidence items are appended to items.

    Only a withdrawal lowers the score (VCF_WITHDRAWN_PENALTY). Interactions are flagged, not scored: the usual
    answer to "osimertinib + carbamazepine" is to change the carbamazepine, not to rank an unapproved drug above
    osimertinib. Boxed warnings are flagged too (most anticancer drugs carry one), and so are interactions inside a
    combination regimen, which its protocol manages."""
    cfg = current_app.config
    index = drug_synonym_index()
    meds = [resolve_drug(m, index) for m in medications]
    rows = [c for rows in tracks.values() for c in rows]
    for c in rows:
        c["components"] = components(c["drug"], index)
    warnings = warnings_for([d for c in rows for d in c["components"]])

    # Drug–drug checks for the drugs most likely to be read: the top of each track.
    checked = [c for name, track in tracks.items() for c in track[: cfg["MAX_DDI_DRUGS"]]]
    subjects = list({strip_salt(d["name"]): d for c in checked for d in c["components"]}.values())
    regimens = [c["components"] for c in checked if len(c["components"]) > 1]
    alerts = check_interactions(subjects, meds, regimens, status)
    items += evidence_items(alerts, start=len(items) + 1)

    for c in rows:
        names = {strip_salt(d["name"]) for d in c["components"]}
        mine = [a for a in alerts if (a["context"] == "current medication" and
                                      (strip_salt(a["drug"]) in names or strip_salt(a["other"]) in names))
                or (a["context"] == "combination regimen" and {strip_salt(a["drug"]), strip_salt(a["other"])} <= names)]
        own = [w for d in c["components"] for w in warnings.get(d.get("chembl_id"), [])]
        withdrawn = any(w["type"] == "Withdrawn" for w in own)
        med_alerts = [a for a in mine if a["context"] == "current medication"]
        points = [("withdrawn from the market (ChEMBL)", cfg["VCF_WITHDRAWN_PENALTY"])] if withdrawn else []
        c["safety"] = {
            "warnings": warning_summary(own),
            "withdrawn": withdrawn,
            "interactions": [{"evidence_id": a["evidence_id"], "severity": a["severity"], "context": a["context"],
                              "drug": a["drug"], "other": a["other"], "mechanism": a["mechanism"],
                              "summary": a["summary"]} for a in mine],
            "checked": c in checked,
            "worst": worst(mine),
            "medication_alert": worst(med_alerts),
        }
        if points:
            c["score_parts"] = c["score_parts"] + [{"label": text, "points": p} for text, p in points]
            c["score"] += sum(p for _, p in points)
            c["track_reason"] += "; down-ranked: " + ", ".join(text for text, _ in points)
        c["evidence_ids"] = list(dict.fromkeys(c["evidence_ids"] + [a["evidence_id"] for a in mine]))
        c.pop("components", None)
    for track in tracks.values():
        track.sort(key=lambda c: (bool(c["strong_resistance"]), -c["score"], c["drug"]))
        for i, c in enumerate(track, start=1):
            c["rank"] = i
            c["top"] = i <= cfg["VCF_TOP_PER_TRACK"]
    return {
        "medications": meds,
        "interactions": alerts,
        "checked_drugs": len(subjects),
        "warned_drugs": sum(1 for c in rows if c["safety"]["warnings"]),
    }


# ---------- Gene -> drugs / drug -> genes: warnings and interactions on the DGI table ----------

def check_table(table, searched_drug, medications, items, status):
    """Adds row["warnings"] (ChEMBL) and row["interactions"] (with the current medications) to the graded pairs,
    appends the alerts to items, and returns evidence["safety"].

    searched_drug: the stage 1 entry of a drug search (its own warnings and interactions are what matter), or None
    for a gene search (the best-supported drugs of the table are checked)."""
    cfg = current_app.config
    index = drug_synonym_index()
    meds = [resolve_drug(m, index) for m in medications]
    drugs = {}
    for row in table:
        key = strip_salt(row["drug"])
        if key not in drugs:
            drugs[key] = resolve_drug(row["drug"], index)
    if searched_drug:
        subjects = [{"input": searched_drug.get("input") or searched_drug["name"],
                     "name": resolve_drug(searched_drug["name"], index)["name"],
                     "chembl_id": searched_drug.get("id") or drugs.get(strip_salt(searched_drug["name"]), {}).get("chembl_id")}]
    else:
        ordered = [r for r in table if r["grade"] != "limited"] + [r for r in table if r["grade"] == "limited"]
        subjects = list({strip_salt(r["drug"]): drugs[strip_salt(r["drug"])] for r in ordered}.values())
        subjects = subjects[: cfg["MAX_DDI_DRUGS"]]
    warnings = warnings_for(list(drugs.values()) + subjects)
    alerts = check_interactions(subjects, meds, [], status) if meds else []
    if not meds:
        status["openfda"] = {"state": "skipped", "count": 0}
    items += evidence_items(alerts, start=len(items) + 1)
    for row in table:
        d = drugs[strip_salt(row["drug"])]
        row["chembl_id"] = row.get("chembl_id") or d.get("chembl_id")
        row["warnings"] = warning_summary(warnings.get(d.get("chembl_id")))
        names = {strip_salt(row["drug"]), strip_salt(d["name"])}
        row["interactions"] = [{"evidence_id": a["evidence_id"], "severity": a["severity"], "other": a["other"],
                                "mechanism": a["mechanism"], "summary": a["summary"]}
                               for a in alerts if strip_salt(a["drug"]) in names or strip_salt(a["other"]) in names]
    drug_warnings = warning_summary(warnings.get(subjects[0].get("chembl_id"))) if searched_drug and subjects else []
    return {"medications": meds, "interactions": alerts, "checked_drugs": len(subjects) if meds else 0,
            "drug_warnings": drug_warnings,
            "warned_drugs": sum(1 for d in drugs.values() if warnings.get(d.get("chembl_id")))}
