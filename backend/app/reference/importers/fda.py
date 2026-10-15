"""FDA Table of Pharmacogenomic Biomarkers in Drug Labeling (US government work, public domain).

The table is only published as an HTML page, so it is parsed from the page. Each biomarker cell is mapped to
HGNC symbols where it names them exactly: "ERBB2 (HER2)" → ERBB2, "BCR-ABL1 (Philadelphia chromosome)" →
BCR, ABL1, "ESR, PGR (Hormone Receptor)" → PGR only ("ESR" is not a gene symbol).
"""

import re
from datetime import datetime
from html.parser import HTMLParser

from flask import current_app

from ..models import FdaPgxBiomarker
from ..text import norm
from . import drug_synonym_index, gene_symbol_index, http_session, lookup_drug, replace_tables

EXPECTED_HEADER = ["Drug", "Therapeutic Area", "Biomarker", "Labeling Sections"]


def run(echo):
    url = current_app.config["FDA_BIOMARKERS_URL"]
    echo(f"[fda] downloading {url}")
    resp = http_session().get(url, timeout=120)
    resp.raise_for_status()
    rows, current_as_of = parse_table(resp.text)
    records = build_biomarkers(rows, gene_symbol_index(), drug_synonym_index())
    replace_tables([(FdaPgxBiomarker, records)])
    mapped = sum(1 for r in records if r["gene_symbol"])
    return {"version": f"FDA PGx biomarker table, content current as of {current_as_of or 'unknown'}", "url": url,
            "counts": {"fda_pgx_biomarkers": len(records), "table_rows": len(rows), "linked_to_hgnc": mapped}}


class _TableParser(HTMLParser):
    """Collects the text of every cell of every <tr>."""

    def __init__(self):
        super().__init__()
        self.rows, self._row, self._cell = [], None, None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def parse_table(page):
    """Returns ([[drug, area, biomarker, sections]], "YYYY-MM-DD" or None). Fails loudly if the layout changed."""
    match = re.search(r"<table.*?</table>", page, re.S | re.I)
    if not match:
        raise ValueError("FDA page has no table; the page layout may have changed")
    parser = _TableParser()
    parser.feed(match.group(0))
    header, *rows = parser.rows
    if [re.sub(r"[^A-Za-z ]", "", h).strip() for h in header] != EXPECTED_HEADER:
        raise ValueError(f"Unexpected FDA table header: {header}")
    current = re.search(r"Content current as of:\s*(?:<[^>]+>\s*)*(\d{2}/\d{2}/\d{4})", page)
    current_as_of = datetime.strptime(current.group(1), "%m/%d/%Y").date().isoformat() if current else None
    return [r for r in rows if len(r) == 4 and r[0]], current_as_of


def biomarker_genes(biomarker, genes):
    """HGNC symbols named in a biomarker cell; [] for non-gene biomarkers."""
    names = re.sub(r"\(.*?\)", "", biomarker)  # drop "(HER2)", "(Hormone Receptor)"
    found = []
    for token in re.split(r"[,;]|::", names):
        token = norm(token)
        if not token:
            continue
        if token in genes:
            found.append(token)
        elif "-" in token:  # fusions such as BCR-ABL1 or CCDC6-RET, but HLA-B is matched whole above
            found += [part for part in token.split("-") if part in genes]
    return list(dict.fromkeys(found))


def build_biomarkers(rows, genes, drugs):
    out = []
    for number, (label, area, biomarker, sections) in enumerate(rows, start=1):
        drug_name = re.sub(r"\s*\(\d+\)$", "", label)  # "Abemaciclib (2)" is the drug's second biomarker row
        record = {
            "row_number": number,
            "drug_label": label[:255],
            "drug_name": drug_name[:255],
            "drug_name_norm": norm(drug_name)[:255],
            "chembl_id": lookup_drug(drugs, drug_name),
            "therapeutic_area": area[:128] or None,
            "biomarker": biomarker[:255],
            "labeling_sections": [s.strip() for s in sections.split(",") if s.strip()],
        }
        symbols = biomarker_genes(biomarker, genes)
        for symbol in symbols or [None]:
            out.append({**record, "gene_symbol": symbol, "hgnc_id": genes.get(symbol) if symbol else None})
    return out
