"""Parse an uploaded gene panel into a list of gene symbols.

Accepts a plain list (one per line, or comma/space separated) or a table (csv/tsv) whose
header row has a gene column such as "gene", "symbol" or "gene_symbol"; for tables only
that column is read.
"""

import re

SYMBOL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9\-\.]{0,19}$")
HEADER_WORDS = {"GENE", "GENES", "SYMBOL", "GENE_SYMBOL", "GENESYMBOL", "GENE SYMBOL", "HGNC", "HGNC_SYMBOL"}
DELIMITER_RE = re.compile(r"[,\t;]")


def _clean(token):
    return token.strip().strip('"').strip("'").strip()


def _tokens(text):
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    header = [_clean(h).upper() for h in DELIMITER_RE.split(lines[0])]
    gene_col = next((i for i, h in enumerate(header) if h in HEADER_WORDS), None)
    if gene_col is not None and len(header) > 1:
        rows = (DELIMITER_RE.split(line) for line in lines[1:])
        return [row[gene_col] for row in rows if len(row) > gene_col]
    tokens = re.split(r"[\s,;\t]+", text)
    return [t for t in tokens if _clean(t).upper() not in HEADER_WORDS]


def parse_gene_panel(text, max_genes):
    genes, ignored = [], []
    for token in _tokens(text):
        token = _clean(token)
        if not token:
            continue
        if not SYMBOL_RE.match(token):
            ignored.append(token)
        elif token.upper() not in genes:
            genes.append(token.upper())
    return {"genes": genes[:max_genes], "ignored": ignored[:50], "truncated": len(genes) > max_genes}
