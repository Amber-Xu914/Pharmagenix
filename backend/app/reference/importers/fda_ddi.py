"""FDA "Examples of Drugs that Interact with CYP Enzymes and Transporter Systems" (US government work, public domain).

One table of ~245 drugs and other substances, each with its clinical role on CYP enzymes (strong / moderate / weak
inhibitor or inducer, sensitive or moderately sensitive substrate) and on transporters (inhibitor, substrate).
The FDA classifies by effect on exposure: a strong inhibitor raises the AUC of a sensitive substrate >= 5-fold, a
strong inducer lowers it by >= 80 %. It is a list of examples, not a complete one; drug labels (openFDA, used live
by services/safety.py) cover the drugs it leaves out.
"""

import html
import re

from flask import current_app

from ..models import DrugCypRole
from ..text import norm
from . import drug_synonym_index, gene_symbol_index, http_session, lookup_drug, replace_tables

EXPECTED_HEADER = ["Drug or Other Substance", "CYP Strg INH", "CYP Mod INH", "CYP WK INH", "CYP Strg IND",
                   "CYP Mod IND", "CYP WK IND", "CYP SENS SUB", "CYP Mod SENS SUB", "TRNSP INH", "TRNSP SUB"]
# column -> (system, role, strength)
COLUMNS = {
    "CYP Strg INH": ("CYP", "inhibitor", "strong"), "CYP Mod INH": ("CYP", "inhibitor", "moderate"),
    "CYP WK INH": ("CYP", "inhibitor", "weak"), "CYP Strg IND": ("CYP", "inducer", "strong"),
    "CYP Mod IND": ("CYP", "inducer", "moderate"), "CYP WK IND": ("CYP", "inducer", "weak"),
    "CYP SENS SUB": ("CYP", "substrate", "sensitive"), "CYP Mod SENS SUB": ("CYP", "substrate", "moderate sensitive"),
    "TRNSP INH": ("transporter", "inhibitor", None), "TRNSP SUB": ("transporter", "substrate", None),
}
CYP = re.compile(r"\b(1A2|2B6|2C8|2C9|2C19|2D6|2E1|3A4?)\b")
TRANSPORTER = re.compile(r"P-gp|BCRP|OATP1B1|OATP1B3|OATP1B(?![13])|MATE1|MATE2-K|OAT1|OAT3|OCT1|OCT2")
# The gene behind each pathway (CYP3A is mostly CYP3A4; OATP1B means both OATP1B1 and OATP1B3).
GENES = {"CYP3A": ["CYP3A4"], "P-gp": ["ABCB1"], "BCRP": ["ABCG2"], "OATP1B1": ["SLCO1B1"], "OATP1B3": ["SLCO1B3"],
         "OATP1B": ["SLCO1B1", "SLCO1B3"], "MATE1": ["SLC47A1"], "MATE2-K": ["SLC47A2"], "OAT1": ["SLC22A6"],
         "OAT3": ["SLC22A8"], "OCT1": ["SLC22A1"], "OCT2": ["SLC22A2"]}


def run(echo):
    url = current_app.config["FDA_DDI_TABLE_URL"]
    echo(f"[fda_ddi] downloading {url}")
    resp = http_session().get(url, timeout=120, headers={"User-Agent": "Mozilla/5.0 (DGI-Explorer academic project)"})
    resp.raise_for_status()
    rows, footnotes, current_as_of = parse_page(resp.text)
    records = build_roles(rows, footnotes, gene_symbol_index(), drug_synonym_index())
    replace_tables([(DrugCypRole, records)])
    return {"version": f"FDA CYP / transporter interaction table, content current as of {current_as_of or 'unknown'}",
            "url": url, "counts": {"drug_cyp_roles": len(records), "substances": len(rows),
                                   "linked_to_chembl": len({r["drug_name"] for r in records if r["chembl_id"]})}}


def _text(fragment):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def parse_page(page):
    """([[cell, ...]], {footnote number: text}, "MM/DD/YYYY" or None). Fails loudly if the layout changed."""
    table = re.search(r"<table.*?</table>", page, re.S | re.I)
    if not table:
        raise ValueError("FDA page has no table; the page layout may have changed")
    rows = [[_text(c) for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", r, re.S | re.I)]
            for r in re.findall(r"<tr.*?</tr>", table.group(0), re.S | re.I)]
    header, *body = rows
    if header != EXPECTED_HEADER:
        raise ValueError(f"Unexpected FDA table header: {header}")
    footnotes = {}
    tail = page[table.end():]
    section = re.search(r"Footnotes(.*?)(Content current as of|$)", tail, re.S)
    for line in re.findall(r"<p[^>]*>(.*?)</p>", section.group(1) if section else "", re.S):
        m = re.match(r"(\d+)\s+(.*)", _text(line))
        if m:
            footnotes[int(m.group(1))] = m.group(2)
    current = re.search(r"Content current as of:\s*(?:<[^>]+>\s*)*(\d{2}/\d{2}/\d{4})", page)
    return [r for r in body if len(r) == len(EXPECTED_HEADER) and r[0]], footnotes, current.group(1) if current else None


def _clean_name(name):
    """'ritonavir 14, 15, 16' -> ('ritonavir', [14, 15, 16])."""
    m = re.match(r"^(.*?[^\d\s,])\s+((?:\d+\s*,?\s*)+)$", name)
    if not m:
        return name, []
    return m.group(1).strip(), [int(n) for n in re.findall(r"\d+", m.group(2))]


def pathways(cell, system):
    """'1A2, 2B6; 2C8; 2C9 moderate inducer' -> ['CYP1A2', 'CYP2B6', 'CYP2C8', 'CYP2C9']."""
    if system == "CYP":
        found = ["CYP3A" if p.startswith("3A") else f"CYP{p}" for p in CYP.findall(cell)]
    else:
        found = TRANSPORTER.findall(cell)
    return list(dict.fromkeys(found))


def build_roles(rows, footnotes, genes, drugs):
    out = []
    for row in rows:
        name, notes = _clean_name(row[0])
        notes += [int(n) for cell in row[1:] for n in re.findall(r"(?<![A-Z0-9-])(\d{1,2})(?=\s*(?:;|,|$| [a-z]))", cell)
                  if 0 < int(n) <= max(footnotes, default=0)]
        note_text = " ".join(f"[{n}] {footnotes[n]}" for n in dict.fromkeys(notes) if n in footnotes) or None
        base = {"drug_name": name[:255], "drug_name_norm": norm(name)[:255], "chembl_id": lookup_drug(drugs, name),
                "notes": note_text}
        for column, cell in zip(EXPECTED_HEADER[1:], row[1:]):
            if not cell:
                continue
            system, role, strength = COLUMNS[column]
            for pathway in pathways(cell, system):
                for symbol in GENES.get(pathway, [pathway] if system == "CYP" else []):
                    out.append({**base, "system": system, "pathway": pathway, "gene_symbol": symbol,
                                "hgnc_id": genes.get(symbol), "role": role, "strength": strength})
    return out
