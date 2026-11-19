"""Downloadable Word report (.docx) for a query, following the team's report structure (docs/report_templates and
Report_Structure.pdf): a page-1 case table and brief results, then Abstract, 1 Introduction, 2 Methodology,
3 Results, 4 Interpretation, 5 Limitations, 6 Conclusion and References, with appendices.

Everything is built from the stored query by fixed rules (the tables, tiers, grades, scores and safety checks the
results page shows; the methodology from the configuration and the code's own constants), except the LLM text: the
Analysis agent's interpretation and, for a patient VCF, the Clinical summary agent's brief results, regimen options
and monitoring plan. LLM text is reproduced as stored, with its evidence IDs ([E3]) renumbered to the reference list;
statements it marks [GK] come from general clinical knowledge, not the retrieved evidence, and are labelled as such.

The package is assembled from report_assets/report_base.docx (styles, page setup, header and footer) with a new
body, header text and an optional network figure, using plain OOXML: no extra dependency. Heat maps are tables with
shaded cells, so they need no image library.
"""

import re
import struct
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from flask import current_app

from .relations import DEFAULT_RELATION_TYPES, labels

BASE_DOCX = Path(__file__).resolve().parent.parent / "report_assets" / "report_base.docx"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
CONTENT_WIDTH = 9412          # text width of the base layout, in twentieths of a point
FIGURE_MAX_EMU = 5_760_000    # 16 cm
MAX_FIGURE_BYTES = 8_000_000
ENDMATTER = ("Tables, tiers, grades, scores and safety checks were produced by deterministic rules from the cited "
             "sources; only the text marked as written by a language model is machine-written.")
AI_ROLES = {"primary": "Analysis", "clinical": "Clinical summary", "literature": "Literature",
            "biology": "Biological interpretation", "hypothesis": "Hypotheses"}
GRADE_ORDER = ["strong", "moderate", "supportive", "limited"]
PGKB_ORDER = ["1A", "1B", "2A", "2B", "3", "4"]
RESISTANT = {"Resistance", "Reduced Sensitivity"}
SENSITIVE = {"Sensitivity/Response"}
LEVEL_ORDER = "ABCDE"

# Heat-map colours (shading): CIViC levels A–E dark to light blue, resistance red, nothing grey
LEVEL_FILL = {"A": "1F4E8C", "B": "3A7BD5", "C": "8FB3E8", "D": "C5D9F3", "E": "E3EDFA"}
RESIST_FILL, NONE_FILL, LABEL_FILL, NOTE_FILL = "B22222", "EEEEEE", "F2F2F2", "F4F4F4"
GRADE_FILL = {"strong": "1F4E8C", "moderate": "3A7BD5", "supportive": "C5D9F3", "limited": "EEEEEE"}

SOURCE_INFO = {  # key: (display name, what it contributes)
    "vep": ("Ensembl VEP", "Consequence, HGVS, population frequency, pathogenicity scores"),
    "civic": ("CIViC", "Variant-level clinical evidence (response and resistance, levels A–E)"),
    "oncokb": ("OncoKB", "Variant-level therapeutic levels"),
    "dgidb": ("DGIdb", "Drug–gene interactions and types (gene level)"),
    "chembl": ("ChEMBL", "Curated mechanisms of action"),
    "cpic": ("CPIC", "Genotype-based prescribing guidelines"),
    "pharmgkb": ("PharmGKB / ClinPGx", "Clinical annotations of variants"),
    "pubtator": ("PubTator3", "Literature"),
    "medcpt": ("MedCPT", "Re-ranking of the retrieved literature"),
    "uniprot": ("UniProt", "Protein function"),
    "drugbank": ("DrugBank", "Drug descriptions"),
    "fda_ddi": ("FDA drug-interaction table", "CYP / transporter inhibitors, inducers and substrates"),
    "openfda": ("openFDA drug labels", "Drug interactions sections of the labels"),
    "pbs": ("PBS Schedule", "Australian subsidy listing"),
}
SOURCE_NAMES = {k: v[0] for k, v in SOURCE_INFO.items()}
NOT_ENABLED_REASON = {"oncokb": "no API token", "drugbank": "no API key"}

# Methods and data sources cited by the methodology (published papers; websites for resources without one)
BIBLIOGRAPHY = {
    "amp": "Li MM, Datto M, Duncavage EJ, et al. Standards and guidelines for the interpretation and reporting of "
           "sequence variants in cancer (AMP/ASCO/CAP). J Mol Diagn. 2017;19(1):4–23.",
    "escat": "Mateo J, Chakravarty D, Dienstmann R, et al. A framework to rank genomic alterations as targets for "
             "cancer precision medicine: the ESMO Scale for Clinical Actionability of molecular Targets (ESCAT). Ann "
             "Oncol. 2018;29(9):1895–1902.",
    "vep": "McLaren W, Gil L, Hunt SE, et al. The Ensembl Variant Effect Predictor. Genome Biol. 2016;17:122.",
    "gnomad": "Chen S, Francioli LC, Goodrich JK, et al. A genomic mutational constraint map using variation in "
              "76,156 human genomes. Nature. 2024;625:92–100.",
    "hgnc": "Seal RL, Braschi B, Gray K, et al. Genenames.org: the HGNC resources in 2023. Nucleic Acids Res. "
            "2023;51(D1):D1003–D1009.",
    "oncotree": "Kundra R, Zhang H, Sheridan R, et al. OncoTree: a cancer classification system for precision "
                "oncology. JCO Clin Cancer Inform. 2021;5:221–230.",
    "hotspots": "Chang MT, Bhattarai TS, Schram AM, et al. Accelerating discovery of functional mutant alleles in "
                "cancer. Cancer Discov. 2018;8(2):174–183.",
    "intogen": "Martínez-Jiménez F, Muiños F, Sentís I, et al. A compendium of mutational cancer driver genes. Nat Rev "
               "Cancer. 2020;20:555–572.",
    "cbioportal": "Gao J, Aksoy BA, Dogrusoz U, et al. Integrative analysis of complex cancer genomics and clinical "
                  "profiles using the cBioPortal. Sci Signal. 2013;6(269):pl1.",
    "alphamissense": "Cheng J, Novati G, Pan J, et al. Accurate proteome-wide missense variant effect prediction with "
                     "AlphaMissense. Science. 2023;381:eadg7492.",
    "revel": "Ioannidis NM, Rothstein JH, Pejaver V, et al. REVEL: an ensemble method for predicting the "
             "pathogenicity of rare missense variants. Am J Hum Genet. 2016;99(4):877–885.",
    "cadd": "Rentzsch P, Witten D, Cooper GM, et al. CADD: predicting the deleteriousness of variants throughout the "
            "human genome. Nucleic Acids Res. 2019;47(D1):D886–D894.",
    "civic": "Griffith M, Spies NC, Krysiak K, et al. CIViC is a community knowledgebase for expert crowdsourcing the "
             "clinical interpretation of variants in cancer. Nat Genet. 2017;49:170–174.",
    "oncokb": "Chakravarty D, Gao J, Phillips SM, et al. OncoKB: a precision oncology knowledge base. JCO Precis "
              "Oncol. 2017;1:PO.17.00011.",
    "dgidb": "Cannon M, Stevenson J, Stahl K, et al. DGIdb 5.0: rebuilding the drug–gene interaction database for "
             "precision medicine and drug discovery platforms. Nucleic Acids Res. 2024;52(D1):D1227–D1235.",
    "chembl": "Zdrazil B, Felix E, Hunter F, et al. The ChEMBL Database in 2023: a drug discovery platform spanning "
              "multiple bioactivity data types and time periods. Nucleic Acids Res. 2024;52(D1):D1180–D1192.",
    "pubtator": "Wei CH, Allot A, Lai PT, et al. PubTator 3.0: an AI-powered literature resource for unlocking "
                "biomedical knowledge. Nucleic Acids Res. 2024;52(W1):W540–W546.",
    "cpic": "Clinical Pharmacogenetics Implementation Consortium. CPIC guidelines. https://cpicpgx.org/guidelines/",
    "pharmgkb": "ClinPGx (PharmGKB). Clinical annotations. https://www.clinpgx.org",
    "uniprot": "UniProt Consortium. UniProt Knowledgebase. https://www.uniprot.org",
    "fda_ddi": "US Food and Drug Administration. For healthcare professionals: FDA's examples of drugs that interact "
               "with CYP enzymes and transporter systems. https://www.fda.gov/CYPandTransporterInteractingDrugs",
    "openfda": "US Food and Drug Administration. openFDA drug labeling API. https://open.fda.gov/apis/drug/label/",
    "pbs": "Australian Government Department of Health. Pharmaceutical Benefits Scheme. https://www.pbs.gov.au",
}
VCF_BIB = ["amp", "escat", "vep", "gnomad", "hgnc", "oncotree", "hotspots", "intogen", "cbioportal", "alphamissense",
           "revel", "cadd", "civic", "oncokb", "dgidb", "chembl", "pubtator", "openfda", "fda_ddi", "pbs"]
DGI_BIB = ["hgnc", "dgidb", "chembl", "cpic", "pharmgkb", "pubtator", "uniprot", "fda_ddi", "openfda", "pbs"]



# ---------- OOXML building blocks ----------

def _t(text):
    return f'<w:t xml:space="preserve">{escape(str(text))}</w:t>'


def run(text, bold=False, italic=False, style=None, rpr=""):
    props = (f'<w:rStyle w:val="{style}"/>' if style else "") + ("<w:b/><w:bCs/>" if bold else "") + \
            ("<w:i/><w:iCs/>" if italic else "") + rpr
    inner = "<w:br/>".join("<w:tab/>".join(_t(part) if part else "" for part in line.split("\t"))
                           for line in str(text).split("\n"))
    rpr_xml = f"<w:rPr>{props}</w:rPr>" if props else ""
    return f"<w:r>{rpr_xml}{inner}</w:r>"


def para(style, *runs, ppr=""):
    body = "".join(r if r.startswith("<") else run(r) for r in runs if r)
    return f'<w:p><w:pPr><w:pStyle w:val="{style}"/>{ppr}</w:pPr>{body}</w:p>'


def bullet(*runs, style="Normal"):
    """A bulleted paragraph (a hanging "•", so no numbering definition is needed)."""
    return para(style, run("•\t"), *runs, ppr='<w:tabs><w:tab w:val="left" w:pos="300"/></w:tabs>'
                                             '<w:ind w:left="300" w:hanging="300"/>')


def _cite_link(n):
    return (f'<w:hyperlink w:history="1" w:anchor="ref_{n}"><w:r><w:rPr><w:rStyle w:val="Citation"/></w:rPr>'
            f'{_t(n)}</w:r></w:hyperlink>')


def cite_runs(numbers):
    """[2–5, 10] with each number linked to its reference; [] for nothing citable."""
    nums = sorted(set(numbers))
    if not nums:
        return []
    groups, start = [], nums[0]
    for prev, cur in zip(nums, nums[1:] + [None]):
        if cur != prev + 1:
            groups.append((start, prev))
            start = cur
    out = [run("[")]
    for i, (a, b) in enumerate(groups):
        if i:
            out.append(run(", "))
        out.append(_cite_link(a))
        if b != a:
            out += [run("–"), _cite_link(b)]
    out.append(run("]"))
    return out


_NONE = '<w:{0} w:val="none" w:color="FFFFFF" w:sz="0"/>'
_LINE = '<w:{0} w:val="single" w:color="{2}" w:sz="{1}"/>'
_CELL_MARGIN = ('<w:tcMar><w:top w:type="dxa" w:w="40"/><w:left w:type="dxa" w:w="80"/>'
                '<w:bottom w:type="dxa" w:w="40"/><w:right w:type="dxa" w:w="80"/></w:tcMar>')


def cell(content, fill=None, bold=False, color=None, center=False):
    """A table cell with options (a plain str or list of runs is a normal cell)."""
    return {"content": content, "fill": fill, "bold": bold, "color": color, "center": center}


def _cell(width, content, style, top=None, bottom=None, header=False, fill=None, inner=False):
    opts = content if isinstance(content, dict) else {"content": content}
    fill = opts.get("fill") or fill
    value = opts.get("content")
    rpr = (f'<w:color w:val="{opts["color"]}"/>' if opts.get("color") else "")
    if isinstance(value, list):
        runs = value
    else:
        runs = [run("–" if value in (None, "") else value, bold=bool(opts.get("bold")), rpr=rpr)]
    rule = "D9D9D9" if inner else "111111"
    borders = "".join([
        _LINE.format("top", top, "111111") if top else _NONE.format("top"), _NONE.format("left"),
        _LINE.format("bottom", bottom, rule) if bottom else _NONE.format("bottom"), _NONE.format("right"),
    ])
    shading = f'<w:shd w:val="clear" w:color="auto" w:fill="{fill}"/>' if fill else ""
    valign = '<w:vAlign w:val="bottom"/>' if header else '<w:vAlign w:val="center"/>' if opts.get("center") else ""
    jc = "center" if opts.get("center") else "left"
    return (f'<w:tc><w:tcPr><w:tcW w:type="dxa" w:w="{width}"/><w:tcBorders>{borders}</w:tcBorders>{shading}'
            f'{_CELL_MARGIN}{valign}</w:tcPr>'
            f'<w:p><w:pPr><w:pStyle w:val="{style}"/><w:jc w:val="{jc}"/></w:pPr>{"".join(runs)}</w:p></w:tc>')


def _widths(weights):
    total = sum(weights)
    widths = [int(CONTENT_WIDTH * w / total) for w in weights]
    widths[-1] += CONTENT_WIDTH - sum(widths)
    return widths


def _tbl_open(widths):
    no_borders = "".join(_NONE.format(side) for side in ("top", "left", "bottom", "right", "insideH", "insideV"))
    grid = "".join(f'<w:gridCol w:w="{w}"/>' for w in widths)
    return (f'<w:tbl><w:tblPr><w:tblW w:type="dxa" w:w="{CONTENT_WIDTH}"/><w:tblBorders>{no_borders}</w:tblBorders>'
            f'</w:tblPr><w:tblGrid>{grid}</w:tblGrid>')


def table(headers, rows, weights, header_fill="EDF1F7", rules=True):
    """Booktabs-style table as in the report template: rules above and below the header and under the last row, a
    light shaded header and thin rules between rows."""
    widths = _widths(weights)
    xml = [_tbl_open(widths),
           '<w:tr><w:trPr><w:cantSplit/><w:tblHeader/></w:trPr>'
           + "".join(_cell(w, h, "TableHeading", top=12, bottom=8, header=True, fill=header_fill)
                     for w, h in zip(widths, headers)) + "</w:tr>"]
    for i, row in enumerate(rows):
        last = i == len(rows) - 1
        xml.append("<w:tr><w:trPr><w:cantSplit/></w:trPr>"
                   + "".join(_cell(w, c, "TableText", bottom=12 if last else (4 if rules else None),
                                   inner=not last) for w, c in zip(widths, row)) + "</w:tr>")
    return "".join(xml) + "</w:tbl>"


def info_table(rows):
    """Page-1 case table: label | value | label | value, labels bold on a grey background."""
    widths = _widths([1.6, 2.6, 1.6, 2.6])
    xml = [_tbl_open(widths)]
    for i, (l1, v1, l2, v2) in enumerate(rows):
        bottom = 8 if i == len(rows) - 1 else 4
        cells = [(l1, True), (v1, False), (l2, True), (v2, False)]
        xml.append("<w:tr><w:trPr><w:cantSplit/></w:trPr>" + "".join(
            _cell(w, {"content": v, "bold": is_label}, "TableText", top=8 if i == 0 else None, bottom=bottom,
                  fill=LABEL_FILL if is_label else None, inner=i < len(rows) - 1)
            for w, (v, is_label) in zip(widths, cells)) + "</w:tr>")
    return "".join(xml) + "</w:tbl>"


def callout(*runs):
    """A shaded box with a thick left rule (the template's "Exploratory — not for treatment decisions" note)."""
    border = ('<w:tcBorders><w:top w:val="none" w:color="FFFFFF" w:sz="0"/>'
              '<w:left w:val="single" w:color="8C8C8C" w:sz="24"/><w:bottom w:val="none" w:color="FFFFFF" w:sz="0"/>'
              '<w:right w:val="none" w:color="FFFFFF" w:sz="0"/></w:tcBorders>')
    return (_tbl_open([CONTENT_WIDTH]) + f'<w:tr><w:tc><w:tcPr><w:tcW w:type="dxa" w:w="{CONTENT_WIDTH}"/>{border}'
            f'<w:shd w:val="clear" w:color="auto" w:fill="{NOTE_FILL}"/>'
            '<w:tcMar><w:top w:type="dxa" w:w="120"/><w:left w:type="dxa" w:w="200"/><w:bottom w:type="dxa" '
            'w:w="120"/><w:right w:type="dxa" w:w="200"/></w:tcMar></w:tcPr>'
            f'<w:p><w:pPr><w:pStyle w:val="Normal"/></w:pPr>{"".join(runs)}</w:p></w:tc></w:tr></w:tbl>')


def _png_size(data):
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("the figure must be a PNG image")
    return struct.unpack(">II", data[16:24])


def figure_xml(rel_id, data, title, description):
    width, height = _png_size(data)
    cx = FIGURE_MAX_EMU
    cy = int(cx * height / width) if width else cx
    if cy > 7_200_000:  # keep the figure on one page
        cx, cy = int(cx * 7_200_000 / cy), 7_200_000
    desc = escape(description, {'"': "&quot;"})
    return (
        '<w:p><w:pPr><w:pStyle w:val="FigureImage"/></w:pPr><w:r><w:drawing>'
        f'<wp:inline distT="0" distB="0" distL="0" distR="0"><wp:extent cx="{cx}" cy="{cy}"/>'
        '<wp:effectExtent t="0" r="0" b="0" l="0"/>'
        f'<wp:docPr id="1" name="figure1" descr="{desc}" title="{escape(title)}"/>'
        '<wp:cNvGraphicFramePr><a:graphicFrameLocks xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'noChangeAspect="1"/></wp:cNvGraphicFramePr>'
        '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        '<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        '<pic:nvPicPr><pic:cNvPr id="0" name="figure1.png"/><pic:cNvPicPr/></pic:nvPicPr>'
        f'<pic:blipFill><a:blip r:embed="{rel_id}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
        f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic></a:graphicData></a:graphic>'
        '</wp:inline></w:drawing></w:r></w:p>'
    )


# ---------- References and the LLM text ----------

CITATION = re.compile(r"\[(E\d+(?:\s*(?:[,;]|[–—-])\s*E?\d+)*)\]")
INLINE = re.compile(r"\*\*(?P<b>.+?)\*\*|(?<![\w*])[*_](?P<i>[^*_\s][^*_]*?)[*_](?![\w*])|`(?P<c>[^`]+)`"
                    r"|\[(?P<lt>[^\]]+)\]\((?P<lu>[^)]+)\)|(?P<gk>\[GK\])|" + CITATION.pattern)
GK_RPR = '<w:color w:val="7F6000"/><w:sz w:val="15"/><w:szCs w:val="15"/>'


class Refs:
    """Evidence items numbered in order of first citation in the document."""

    def __init__(self, items):
        self.items = {i["id"]: i for i in items}
        self.order = []
        self.numbers = {}

    def number(self, eid):
        if eid not in self.items:
            return None
        if eid not in self.numbers:
            self.order.append(eid)
            self.numbers[eid] = len(self.order)
        return self.numbers[eid]

    def cite(self, eids):
        return cite_runs([n for n in (self.number(e) for e in eids or []) if n])

    def cell(self, eids):
        return self.cite(eids) or None


def _expand(group):
    """'E1–E5, E9' -> ['E1', ..., 'E5', 'E9']."""
    out = []
    for part in re.split(r"\s*[,;]\s*", group):
        bounds = re.split(r"\s*[–—-]\s*", part)
        if len(bounds) == 2:
            a, b = (int(x.lstrip("E")) for x in bounds)
            out += [f"E{n}" for n in range(a, b + 1)] if 0 <= b - a <= 50 else [f"E{a}", f"E{b}"]
        else:
            out.append(part.strip())
    return out


def inline_runs(text, refs, bold=False, italic=False):
    """Markdown inline text -> runs: bold, italic, code, links, [GK] markers and evidence citations."""
    out, pos = [], 0
    for m in INLINE.finditer(text or ""):
        if m.start() > pos:
            out.append(run(text[pos:m.start()], bold, italic))
        if m.group("b") is not None:
            out += inline_runs(m.group("b"), refs, True, italic)
        elif m.group("i") is not None:
            out += inline_runs(m.group("i"), refs, bold, True)
        elif m.group("c") is not None:
            out.append(run(m.group("c"), bold, italic, style="Code"))
        elif m.group("lt") is not None:
            out += inline_runs(m.group("lt"), refs, bold, italic)
        elif m.group("gk") is not None:
            out.append(run("[GK]", italic=True, rpr=GK_RPR))
        else:
            cited = refs.cite(_expand(m.group(m.lastindex)))
            out += cited or [run(m.group(0), bold, italic)]  # an ID not in the evidence stays visible as written
        pos = m.end()
    if pos < len(text or ""):
        out.append(run(text[pos:], bold, italic))
    return out


def md_table(lines, refs):
    """A Markdown table (lines starting with "|") -> (headers, rows of runs)."""
    rows = [[c.strip() for c in line.strip().strip("|").split("|")] for line in lines]
    rows = [r for r in rows if not all(re.fullmatch(r":?-{2,}:?", c) for c in r if c)]
    if not rows:
        return None, []
    headers, body = rows[0], rows[1:]
    width = len(headers)
    return headers, [[inline_runs(c, refs) or None for c in (r + [""] * width)[:width]] for r in body]


def markdown_paragraphs(md, refs, style="AIText", heading="AIHeading"):
    """The LLM's Markdown as paragraphs: ## / ### headings, one paragraph per block or list item, tables as tables."""
    out, buffer, table_lines = [], [], []

    def flush():
        if buffer:
            out.append(para(style, *inline_runs(" ".join(buffer), refs)))
            buffer.clear()

    def flush_table():
        if table_lines:
            headers, rows = md_table(table_lines, refs)
            if headers:
                out.append(table(headers, rows, [1] * len(headers)))
            table_lines.clear()

    for raw in (md or "").splitlines():
        line = raw.strip()
        if line.startswith("|"):
            flush()
            table_lines.append(line)
            continue
        flush_table()
        h = re.match(r"^(#{1,6})\s+(.*)$", line)
        item = re.match(r"^(?:[-*+•]|\d+[.)])\s+(.*)$", line)
        if not line or re.match(r"^(-{3,}|\*{3,})$", line):
            flush()
        elif h:
            flush()
            if len(h.group(1)) > 1:  # the "# Title" line repeats the report title
                out.append(para(heading, *inline_runs(h.group(2).strip("# "), refs)))
        elif item:
            flush()
            out.append(bullet(*inline_runs(item.group(1), refs), style=style))
        else:
            buffer.append(line)
    flush()
    flush_table()
    return out


def _authors(names):
    names = names or []
    return ", ".join(names[:3]) + (" et al." if len(names) > 3 else "")

def reference_text(item):
    """One line per evidence item, written like a bibliography entry; the URL is added after it."""
    k, g = item.get("kind"), item.get
    if k == "interaction":
        types = ", ".join(g("interaction_types") or []) or "not curated"
        score = f"; interaction score {g('score'):.3g}" if isinstance(g("score"), (int, float)) else ""
        srcs = f"; contributing sources {', '.join(g('sources'))}" if g("sources") else ""
        return f"DGIdb interaction record: {g('gene')}–{_name(g('drug'))} (type {types}{score}{srcs}). DGIdb v5."
    if k == "mechanism":
        return (f"ChEMBL mechanism of action: {_name(g('drug'))}, {g('mechanism') or g('action_type')} "
                f"({g('target_name') or g('gene')}). {g('release') or 'ChEMBL'}.")
    if k == "guideline":
        return (f"Clinical Pharmacogenetics Implementation Consortium. {g('guideline_name')}: {g('gene')}–{g('drug')}, "
                f"CPIC level {g('cpic_level') or '–'}, PharmGKB level {g('clinpgx_level') or '–'}. "
                f"{g('release') or 'CPIC'}.")
    if k == "variant_annotation":
        pheno = ", ".join(g("phenotype_categories") or [])
        return (f"PharmGKB clinical annotation {g('annotation_id')}: {g('variant')} and {', '.join(g('drugs') or [])}; "
                f"level {g('level')}{', ' + pheno if pheno else ''}. ClinPGx.")
    if k == "publication":
        return (f"{_authors(g('authors'))}{'. ' if g('authors') else ''}{(g('title') or '').rstrip('.')}. "
                f"{g('journal') or ''}{'. ' if g('journal') else ''}{g('year') or ''}. PMID: {g('pmid')}.")
    if k == "profile":
        protein = g("protein") or {}
        if g("entity_type") == "drug":
            return (f"ChEMBL compound {g('chembl_id')}: {_name(g('full_name') or g('entity'))} "
                    f"({g('molecule_type') or 'drug'}; max phase {g('max_phase')}). ChEMBL.")
        return (f"UniProt entry {protein.get('accession') or ''}: {protein.get('protein_name') or g('full_name')} "
                f"({g('entity')}). UniProt.")
    if k == "drug_info":
        return f"DrugBank record: {_name(g('name') or g('drug') or '')}. DrugBank."
    if k == "variant":
        return (f"Variant call from this sample: {g('notation')} ({g('hgvsc') or 'no HGVS'}); "
                f"annotation {g('source')}.")
    if k == "civic":
        pmid = f", PMID: {g('pmid')}" if g("pmid") else ""
        return (f"CIViC evidence item EID{g('evidence_id')}: {g('molecular_profile')} and "
                f"{', '.join(g('therapies') or []) or 'no therapy'}; {(g('significance') or '').lower()}, level "
                f"{g('level')}, {g('disease')}. Primary source: {g('citation') or 'not given'}{pmid}.")
    if k == "fda_cyp":
        return (f"US Food and Drug Administration. Drug development and drug interactions: {_name(g('drug'))} as a "
                f"{g('label')}. {g('release') or 'FDA table of substrates, inhibitors and inducers'}.")
    if k == "ddi":
        label = f" {g('label_title')} prescribing information ({g('label_date') or 'n.d.'}), drug interactions." \
            if g("label_title") else ""
        return (f"Drug–drug interaction check ({g('rule')}): {g('drug')} with {g('other')}, {g('severity')}."
                f"{label} Source: {g('source')}.")
    if k == "genotype":
        return (f"Clinical Pharmacogenetics Implementation Consortium. {g('guideline_name') or 'CPIC guideline'}: "
                f"recommendation for {_name(g('drug'))} in {g('phenotype')} ({_pop(item)}; classification "
                f"{g('classification') or '–'}), matched to the patient's {g('genotype')}. {g('release') or 'CPIC'}.")
    if k == "oncokb":
        drugs = ", ".join(g("drugs") or g("therapies") or [])
        return f"OncoKB: {g('gene') or ''} {g('alteration') or ''} and {drugs}; level {g('level')}. OncoKB.".replace("  ", " ")
    return f"{g('source') or 'Evidence'} record {g('id')}."


def _pop(rec):
    """A genotype recommendation's population in plain words (items saved before the labels: the CPIC code)."""
    from .genotypes import population_label
    return (rec.get("population_label") if "population_label" in rec else population_label(rec.get("population"))) \
        or "All patients"


def _name(name):
    name = name or ""
    return name.title() if name.isupper() else name


def _lc(drug):
    """A drug name inside a sentence: 'Osimertinib' -> 'osimertinib' (names with inner capitals stay as written)."""
    return drug.lower() if drug.istitle() else drug


def _and(words):
    words = [w for w in words if w]
    if len(words) <= 1:
        return "".join(words)
    return ", ".join(words[:-1]) + " and " + words[-1]


def _date(value, time=False):
    """'27 October 2026' (or with ' 13:37 UTC'); no %-d, which Windows' strftime lacks."""
    if not value:
        return "–"
    when = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return f"{when.day} {when:%B %Y}" + (f" {when:%H:%M} UTC" if time else "")


def _pct(x):
    return "–" if x is None else f"{round(x * 100)}%"


def _vaf(x):
    return "–" if x is None else f"{x * 100:.1f}%"



PRESCRIBER_RULES = ("For prescribing by", "The condition must be stable for the prescriber")


# ---------- The document ----------

class Builder:
    def __init__(self, query, figure=None, figure_note=None):
        self.q = query
        self.ev = query.get("evidence") or {}
        self.refs = Refs(self.ev.get("items") or [])
        self.body = []
        self.tables = 0
        self.figures = 0
        self.figure = figure
        self.figure_note = figure_note or {}
        self.number = f"PGX-{query['id']:05d}"
        self.reports = {}
        for r in sorted(query.get("reports") or [], key=lambda r: r["id"]):
            if r.get("status") in ("success", "flagged") and r.get("content_md"):
                self.reports[r["role"]] = r  # the latest of each role

    def add(self, *xml):
        self.body.extend(xml)

    def h1(self, text):
        self.add(para("Heading1", text, ppr='<w:tabs><w:tab w:val="left" w:pos="440"/></w:tabs>'))

    def h2(self, text):
        self.add(para("Heading2", text))

    def h3(self, text):
        self.add(para("Heading2", run(text, italic=True)))

    def p(self, *runs, style="Normal"):
        self.add(para(style, *runs))

    def bullets(self, lines):
        for line in lines:
            self.add(bullet(*(line if isinstance(line, list) else [run(line)])))

    def table(self, caption, headers, rows, weights, note=None, **kw):
        self.tables += 1
        self.add(para("Caption", run(f"Table {self.tables}. ", bold=True), caption), table(headers, rows, weights, **kw))
        if note:
            self.add(para("TableNote", note))
        self.add(para("Normal"))  # space before the next paragraph, as in the template
        return self.tables

    def figure_table(self, caption, headers, rows, weights):
        """A heat map drawn as a shaded table, captioned as a figure."""
        self.figures += 1
        self.add(table(headers, rows, weights, rules=False),
                 para("FigureCaption", run(f"Figure {self.figures}. ", bold=True), caption))
        return self.figures

    def network_figure(self, caption):
        if not self.figure:
            return None
        self.figures += 1
        self.add(figure_xml("rIdFigure1", self.figure, f"Figure {self.figures}", caption),
                 para("FigureCaption", run(f"Figure {self.figures}. ", bold=True), caption))
        return self.figures

    def llm(self, role, note, sections=None):
        """An LLM report reproduced as stored (or only the named Markdown sections of it)."""
        r = self.reports.get(role)
        if not r:
            return False
        text = r["content_md"]
        if sections is not None:
            text = "\n\n".join(sections)
        flag = " Its output did not pass the automatic check (see Appendix)." if r.get("status") == "flagged" else ""
        self.add(para("AINote", note.format(model=r["model"]) + flag))
        self.add(*markdown_paragraphs(text, self.refs))
        return True

    def sources(self):
        """(enabled names, not enabled "Name (reason)") from the query's source status."""
        status = self.ev.get("source_status") or {}
        on = [SOURCE_NAMES.get(k, k) for k, s in status.items() if s.get("state") == "ok"]
        off = [f"{SOURCE_NAMES.get(k, k)} ({NOT_ENABLED_REASON.get(k, s.get('error') or 'not enabled')})"
               for k, s in status.items() if s.get("state") == "disabled"]
        failed = [f"{SOURCE_NAMES.get(k, k)} (failed)" for k, s in status.items() if s.get("state") == "error"]
        return on, off + failed

    def sources_table(self, keys, note="Records: number retrieved for this query."):
        status = self.ev.get("source_status") or {}
        rows = []
        for key in keys:
            s = status.get(key)
            if not s:
                continue
            name, content = SOURCE_INFO.get(key, (key, ""))
            state = s.get("state")
            version = s.get("version") or {"ok": "live API, accessed at query time", "disabled": "not configured",
                                           "error": "failed at query time", "skipped": "not run"}.get(state, state)
            records = s.get("count") if state == "ok" else {"disabled": "not used", "skipped": "not run"}.get(state, "failed")
            rows.append([name, content, version, records])
        return self.table("Data sources, versions and records retrieved for this query.",
                          ["Source", "Content used", "Version / access", "Records"], rows, [2.2, 3.4, 3.0, 1.0],
                          note=note)

    def page_one(self, title, important, info_rows):
        self.add(para("Title", title))
        self.add(para("Notice", run("Important: ", bold=True, italic=True), run(important, italic=True)))
        self.add(info_table(info_rows), para("Normal"))

    def brief_results(self, rows, note=None):
        self.add(para("Heading2", "Brief Results"))
        self.add(table(["Finding", "Detail"], [[cell(k, bold=True), v] for k, v in rows], [2.2, 7.2]))
        if note:
            self.add(para("TableNote", note))
        self.add(para("Normal"))

    def abstract(self, parts, keywords):
        self.add(para("AbstractHeading", "Abstract"))
        for label, text in parts:
            self.add(para("AbstractText", run(f"{label}. ", bold=True), text))
        self.add(para("Keywords", run("Keywords: ", bold=True), "; ".join(keywords)))

    def references(self, bib_keys):
        self.add(para("Heading1", "References"))
        if self.refs.order:
            self.p("Evidence records cited in this report (numbered in order of first citation):")
        for n, eid in enumerate(self.refs.order, 1):
            item = self.refs.items[eid]
            url = item.get("url")
            self.add(
                f'<w:p><w:pPr><w:pStyle w:val="Reference"/><w:tabs><w:tab w:val="left" w:pos="520"/></w:tabs></w:pPr>'
                f'<w:bookmarkStart w:name="ref_{n}" w:id="{n}"/>{run(f"[{n}]")}<w:bookmarkEnd w:id="{n}"/>'
                f'{run(chr(9))}{run(reference_text(item))}'
                + (run(f" {url}", style="Code", rpr='<w:color w:val="1A3D7C"/><w:sz w:val="14"/><w:szCs w:val="14"/>')
                   if url else "") + "</w:p>")
        self.add(para("Heading2", "Methods and data sources"))
        for key in bib_keys:
            self.add(para("Reference", BIBLIOGRAPHY[key]))

    def provenance(self, heading, extra_rows):
        if heading:
            self.h1(heading)
        rows = [["Query", f"#{self.q['id']}, {_date(self.q.get('created_at'), time=True)}"],
                ["Requested by", "signed-in user" if self.q.get("user_id") else "anonymous user"], *extra_rows]
        for role, r in sorted(self.reports.items(), key=lambda kv: list(AI_ROLES).index(kv[0]) if kv[0] in AI_ROLES else 9):
            template = f", prompt template v{r['template_version']}" if r.get("template_version") else ""
            latency = f"; {r['latency_ms']} ms" if r.get("latency_ms") else ""
            rows.append([f"Language model ({AI_ROLES.get(role, role)})",
                         f"{r['model']}{template}; {r['status']}; {len(r.get('citations') or [])} citations, "
                         f"{len(r.get('invalid_citations') or [])} invalid{latency}"
                         + (f"; check: {r['error_message']}" if r.get("status") == "flagged" and r.get("error_message")
                            else "")])
        self.table("Query and processing record.", ["Item", "Value"], rows, [2.5, 7])
        self.add(para("Endmatter", f"Pharmagenix report {self.number}. {ENDMATTER}"))


def _temperature(model):
    """The temperature a model was called with, as configured now (Claude: ANTHROPIC_TEMPERATURE; reasoning models
    take none)."""
    cfg = current_app.config
    key = "ANTHROPIC" if str(model).startswith("claude") else "OPENAI"
    value = cfg.get(f"{key}_TEMPERATURE")
    return f"temperature {value:g}" if value is not None else "the model's default temperature"


def _md_section(text, heading):
    """The body of one '## heading' section of an LLM report ('' if absent)."""
    from .variants.clinical import sections
    return sections(text).get(heading, "")


def _inline(text, refs):
    """One-paragraph text (bullets joined) as runs, for a table cell."""
    lines = [re.sub(r"^\s*(?:[-*+•]|\d+[.)])\s+", "", line).strip() for line in (text or "").splitlines()]
    joined = " ".join(line for line in lines if line and not line.startswith("|"))
    return inline_runs(joined, refs) or None


def _limitation_bullets(b, extra):
    b.bullets([run(t) if isinstance(t, str) else t for t in extra])

def _restrictions(a):
    """The indications a PBS listing is restricted to (the first paragraph of each restriction), or the kind of rule."""
    rs = a.get("restrictions") or []
    if not rs:
        return "no restriction"
    indications = []
    for r in rs:
        first = re.findall(r"<p[^>]*>(.*?)</p>", r.get("html") or "", re.S)
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", first[0])).strip() if first else ""
        if text and not text.startswith(PRESCRIBER_RULES) and text not in indications:
            indications.append(text)
    if not indications:
        return "prescriber or quantity rules only"
    shown = "; ".join(t if len(t) <= 90 else t[:89] + "…" for t in indications[:3])
    return shown + (f"; +{len(indications) - 3} more" if len(indications) > 3 else "")


# ---------- Patient VCF → candidate drugs ----------



# ---------- Patient VCF → candidate drugs ----------

QC_STEPS = [  # (label, pattern on the audit reason)
    ("FILTER ≠ PASS", r"^FILTER is"),
    ("Read depth below the minimum", r"^depth"),
    ("ALT-supporting reads below the minimum", r"ALT reads <"),
    ("VAF below the minimum", r"tumour VAF|no VAF"),
    ("Germline: population frequency (gnomAD)", r"gnomAD"),
    ("Germline: present in the matched normal", r"matched normal"),
    ("Not protein-altering", r"consequence|not protein-altering|synonymous|intron|non-coding|noncoding"),
    ("Over the annotation limit", r"annotation limit"),
]


def _case_label(inp):
    try:
        from .vcf_explorer import mock_examples
        label = next((e["label"] for e in mock_examples() if e["id"] == inp.get("example")), None)
    except Exception:  # noqa: BLE001 - the label is cosmetic
        label = None
    if label:
        return f"{label} (synthetic)"
    return f"{inp.get('filename') or 'uploaded file'} (uploaded test data)"


def _role(v):
    role = {"Act": "Oncogene", "LoF": "Tumour suppressor"}.get(v.get("driver_role"))
    if not role:
        return "–"
    if v.get("classification") in ("nonsense", "frameshift", "splice") and role == "Tumour suppressor":
        return "Tumour suppressor (loss)"
    if v.get("classification") == "missense" and role == "Oncogene":
        return "Oncogene (activating)"
    return role


def _part_column(label, points):
    low = label.lower()
    if points < 0:
        return "penalty"
    if low.startswith(("civic level", "oncokb")):
        return "level"
    if low in ("same cancer type", "related lung cancer", "other tumour type"):
        return "cancer"
    if low.endswith("variant match"):
        return "match"
    if low.startswith("marketed drug"):
        return "approved"
    return "other"


def _points_fill(points):
    if points < 0:
        return RESIST_FILL, "FFFFFF"
    if points == 0:
        return NONE_FILL, None
    return ("1F4E8C" if points >= 40 else "3A7BD5" if points >= 25 else "8FB3E8" if points >= 10 else "C5D9F3",
            "FFFFFF" if points >= 25 else None)


def _evidence_cells(c, items, variants_by_gene):
    """variant id -> (text, fill, colour) for one drug: best response level, resistance, or a gene-level link."""
    response, resist = {}, {}
    for eid in c.get("evidence_ids") or []:
        item = items.get(eid) or {}
        if item.get("kind") == "civic" and item.get("direction") == "Supports":
            target = resist if item.get("significance") in RESISTANT else response \
                if item.get("significance") in SENSITIVE else None
            if target is not None:
                for vid in item.get("variant_ids") or []:
                    if item.get("level") and (vid not in target or LEVEL_ORDER.index(item["level"])
                                              < LEVEL_ORDER.index(target[vid])):
                        target[vid] = item["level"]
        elif item.get("kind") == "oncokb":
            for vid in item.get("variant_ids") or []:
                (resist if item.get("significance") == "Resistance" else response).setdefault(vid, "A")
    out = {}
    for vid, level in response.items():
        out[vid] = (level, LEVEL_FILL[level], "FFFFFF" if level in "AB" else None)
    for vid, level in resist.items():
        out[vid] = (f"R ({level})", RESIST_FILL, "FFFFFF")
    for g in c.get("gene_links") or []:
        for vid in variants_by_gene.get(g["gene"], []):
            out.setdefault(vid, ("gene", "E3EDFA", None))
    return out


def _sum_text(c):
    """'50 + 30 + 15 + 5 − 60' from a candidate's score parts."""
    out = []
    for i, p in enumerate(c.get("score_parts") or []):
        value = p["points"]
        out.append(str(value) if i == 0 else (f"+ {value}" if value >= 0 else f"− {abs(value)}"))
    return " ".join(out) or "0"


def build_vcf(b):
    from .variants.clinical import not_provided, summary as clinical_summary
    from .variants.parse import PROTEIN_ALTERING
    from .variants.pipeline import (
        GENE_LEVEL_POINTS, LEVEL_POINTS, LITERATURE_MAX, LITERATURE_POINTS, NO_BENEFIT_PENALTY, SPECIFICITY_POINTS,
        STRONG_RESISTANCE_PENALTY, SUBTYPE_POINTS, WEAK_RESISTANCE_PENALTY,
    )

    cfg = current_app.config
    q, ev = b.q, b.ev
    inp = ev.get("input") or {}
    cancer = inp.get("cancer_type") or {}
    code, cancer_name = cancer.get("code") or "?", cancer.get("name") or "unknown cancer type"
    lineage = " → ".join(cancer.get("lineage") or [code])
    variants = ev.get("variants") or {}
    counts = variants.get("counts") or {}
    tiered = variants.get("tiered") or []
    low = variants.get("low_priority") or []
    passed = tiered + low
    audit = variants.get("qc_audit") or []
    tracks = ev.get("tracks") or {}
    established, exploratory = tracks.get("established") or [], tracks.get("exploratory") or []
    a_top = [c for c in established if c.get("top")]
    b_top = [c for c in exploratory if c.get("top")]
    resist_only = ev.get("resistance_only") or []
    safety = ev.get("safety") or {}
    alerts = safety.get("interactions") or []
    meds = [m["name"] for m in safety.get("medications") or []]
    params = ev.get("parameters") or {}
    qc, t3 = params.get("qc") or {}, params.get("tier3") or {}
    items = b.refs.items
    clinical = inp.get("clinical") or {}
    clin_text = (b.reports.get("clinical") or {}).get("content_md") or ""
    sec = lambda h: _md_section(clin_text, h)  # noqa: E731
    top = next((c for c in established if not c.get("strong_resistance")), None)
    down = [c for c in established + exploratory if c.get("strong_resistance")]
    not_rec = [c["drug"] for c in down] + [c["drug"] for c in resist_only]
    t12 = counts.get("tier1", 0) + counts.get("tier2", 0)
    t3n = counts.get("tier3", 0) + counts.get("low_priority", 0)
    on, off = b.sources()
    total = counts.get("total", 0)
    sample = inp.get("filename") or inp.get("example") or q["entities"][0]
    variant_item = {i.get("variant_id"): i["id"] for i in items.values() if i.get("kind") == "variant"}

    # ----- page 1 -----
    b.page_one(
        "Drug–Gene Interaction Explorer: Candidate Drug Report",
        "This report uses synthetic test data. It is not a real patient and not a treatment recommendation. Tiering, "
        "scoring and ranking are rule-based; the language models only explain the results and cannot change any tier "
        "or rank. Statements marked [GK] come from general clinical knowledge, not from the retrieved evidence.",
        [("Case ID", _case_label(inp), "Report ID / date", f"{b.number} · {_date(datetime.now(timezone.utc))}"),
         ("Cancer type (OncoTree)", lineage, "Genome assembly", inp.get("assembly") or "GRCh38"),
         ("Input", ("Tumour VCF + matched normal" if inp.get("normal_sample") else "Tumour VCF")
          if inp.get("format") != "maf" else "MAF", "Variants passing QC",
          f"{total} records in the file → {counts.get('qc_passed', 0)} passed QC ({t12} Tier 1–2, {t3n} Tier 3 or "
          f"low priority)"),
         ("Clinical context", clinical_summary(clinical) or "Not provided", "Not provided",
          ", ".join(x[:1].upper() + x[1:] if i == 0 else x for i, x in enumerate(not_provided(clinical, meds)))
          or "–"),
         ("Sources enabled", ", ".join(on) or "–", "Not enabled", ", ".join(off) or "–"),
         ("Reviewed / authorised by", "______________________", "Signature / date", "______________________")])

    def det_recommended():
        if not top:
            return (ev.get("outcome") or {}).get("no_targeted_therapy") or "No established option."
        return f"{top['drug']} (score {top['score']}; {top['track_reason']})."

    def det_not_rec():
        return (f"{_and(sorted(set(not_rec)))} (resistance from the patient's variants)." if not_rec
                else "No drug was down-ranked for resistance.")

    clinical_r = b.reports.get("clinical")
    pick = lambda h, fallback: _inline(sec(h), b.refs) if clin_text and sec(h) else fallback  # noqa: E731
    co = [v.get("notation") for v in ev.get("co_mutations") or []]
    det_co = ((f"Co-mutations without a targeted drug: {_and(co)}. " if co else "")
              + ("Exploratory hypotheses: " + _and(["{} ({})".format(c["drug"], c["score"]) for c in b_top[:3]]) + "."
                 if b_top else "No exploratory candidate."))
    safety_text = (f"{len(alerts)} drug–drug interaction alert(s) with the current medications or inside regimens "
                   f"(Section 3.4.2)" if alerts else
                   ("No interaction found with the current medications" if meds
                    else "No current medications given; combination regimens checked internally"))
    warned = [c for c in established + exploratory if (c.get("safety") or {}).get("warnings")]
    if warned:
        safety_text += f"; {len(warned)} drug(s) with a ChEMBL warning"
    not_covered = ("Fusions (ALK, ROS1, RET, NTRK, NRG1), copy-number changes (MET, ERBB2 amplification), PD-L1, TMB."
                   + (" OncoKB not queried." if (ev.get("source_status") or {}).get("oncokb", {}).get("state")
                      == "disabled" else ""))
    b.brief_results([
        ("Recommended first-line options", pick("Recommended first-line options", det_recommended())),
        ("Not recommended", pick("Not recommended", det_not_rec())),
        ("Confirm before treating", pick("Confirm before treating", "Not generated (no Clinical summary).")),
        ("Co-mutations and exploratory", pick("Co-mutations and exploratory", det_co)),
        ("At progression", pick("At progression", "Not generated (no Clinical summary).")),
        ("Drug interactions and warnings", safety_text + "."),
        ("Not covered by this test", not_covered),
    ], note=(f"Rows 1–5 were drafted by the Clinical summary agent ({clinical_r['model']}) from the results below; "
             "[n] are numbered references, [GK] marks general clinical knowledge that is not in the retrieved "
             "evidence. Rows 6–7 are rule-based." if clinical_r else
             "No Clinical summary was generated for this query (run before the agent existed, or it failed); rows "
             "1, 2 and 4 are rule-based."))

    # ----- abstract -----
    tier_list = "; ".join(
        f"{_and([v['notation'] for v in tiered if v['tier'] == t])} (Tier {t})"
        for t in (1, 2, 3) if any(v["tier"] == t for v in tiered))
    results = [f"Of {total} records in the file, {counts.get('qc_passed', 0)} protein-altering somatic variants "
               f"passed QC" + (f": {tier_list}" if tier_list else "") + (f"; {len(low)} low priority" if low else "")
               + "."]
    if top:
        results.append(f"{top['drug']} ranked first on the established track (score {top['score']}; "
                       f"{top['track_reason']}).")
    if down:
        names = _and(sorted({_lc(c["drug"]) for c in down}))
        results.append(f"{names[:1].upper() + names[1:]} {'were' if len(down) > 1 else 'was'} "
                       f"down-ranked for resistance from the patient's variants and flagged as not recommended.")
    if b_top:
        results.append("The exploratory track was led by " + _and(["{} ({})".format(_lc(c["drug"]), c["score"])
                                                                    for c in b_top[:2]]) + ", as hypotheses.")
    if alerts:
        results.append(f"{len(alerts)} drug–drug interaction alert(s) were flagged.")
    b.abstract([
        ("Objective", f"To find somatic variants in a tumour {'VCF' if inp.get('format') != 'maf' else 'MAF'} from a "
                      f"{cancer_name.lower()} (OncoTree {code}) case and to rank candidate drugs, keeping established "
                      f"evidence (Track A) strictly separate from exploratory hypotheses (Track B)."),
        ("Methods", "A hybrid pipeline was used. Deterministic tools perform QC, tiering, evidence classification, "
                    "drug safety checks and scoring; language-model agents only explain results, generate labelled "
                    "hypotheses and draft the clinical summary. Each drug receives a rule-based score: evidence level + "
                    "cancer-type match + variant-match specificity + marketed drug − resistance penalty, and each "
                    "drug–variant link is classed as beneficial, potentially harmful or without evidence."),
        ("Results", " ".join(results)),
        ("Conclusion", "The report links every drug to a variant, an evidence source and a score breakdown, gives "
                       "regimen-level options with cautions and a plan for progression (drafted by a language model "
                       "and labelled), and states its data gaps."),
    ], [code, cancer_name, "somatic variants", *sorted({v['gene'] for v in tiered if v.get('gene')}), "variant tiering",
        "CIViC", "drug ranking", "LLM agents"])

    # ----- 1 Introduction -----
    b.h1("1\tIntroduction")
    b.p("Targeted therapy for lung cancer depends on finding specific driver alterations in the tumour. Evidence for "
        "each drug–variant pair is spread across knowledge bases of differing strength. The Drug–Gene Interaction "
        "Explorer (Pharmagenix) takes a tumour's somatic variant file and cancer type and returns ranked candidate "
        "drugs. It works at the variant level and in the specific tumour type, and reports two strictly separated "
        "tracks: established evidence and exploratory hypotheses.")
    b.p("The design is hybrid. Every step that determines what is reported (QC, tiering, evidence classification, "
        "safety checks, scoring and ranking) is deterministic and reproducible. Language-model agents are used only "
        "for flexible tasks, and their output is confined to Track B, to explanatory text and to the labelled clinical "
        f"summary. This prototype covers lung cancer only and was run on {'a synthetic mock case' if inp.get('example') else 'uploaded test data'}.")

    # ----- 2 Methodology -----
    b.h1("2\tMethodology")
    b.p("The workflow has seven stages; this report is assembled from their stored output. Every stage is recorded "
        "with the query (evidence bundle, source versions, trace), so any reported drug can be traced back to its "
        "input record. Language-model agents cannot alter QC decisions, tiers, evidence levels, categories or scores.")
    b.h2("2.1 Input specification")
    b.p("Two inputs are required: (i) a somatic VCF (or MAF) from tumour–normal paired calling, on GRCh38 (GRCh37 is "
        "annotated against Ensembl's GRCh37 server), with per-sample AD and DP for the read-based filters; and (ii) "
        "the cancer type, as free text or an OncoTree code. Multi-allelic records are split into one record per "
        "alternate allele. Optional inputs: the patient's current medications (for drug–drug interaction checks) and "
        "clinical context (stage, treatment status, age, sex, smoking, tumour purity, ECOG, brain metastases), which "
        "only the language-model agents and this report use.")
    b.h2("2.2 Stage 1: VCF quality control")
    b.p("QC uses hard filters only, so the same configuration always gives the same result. Thresholds are set in the "
        "configuration (Table 1). Variants are annotated with Ensembl VEP (one transcript per variant, the canonical "
        "one) unless the file is already annotated (VEP CSQ, SnpEff ANN or ANNOVAR). Only protein-altering "
        "consequences are kept. Every removed record is written to an audit table with the rule that removed it "
        "(Appendix B).")
    n_qc = b.table("QC parameters used for this query (all configurable).", ["Parameter", "Value", "Rationale"], [
        ["FILTER", "= PASS", "Caller-level artefact filters"],
        ["Tumour read depth (DP)", f"≥ {qc.get('min_depth')}", "Minimum coverage for a reliable VAF"],
        ["ALT-supporting reads", f"≥ {qc.get('min_alt_reads')}", "Guards against single-read errors"],
        ["Variant allele fraction (VAF)", f"≥ {_pct(qc.get('min_vaf'))}", "Keeps subclonal drivers"],
        ["Germline: population frequency", f"gnomAD AF > {_pct(qc.get('max_population_af'))} → remove (via VEP)",
         "Common polymorphisms are not tumour-specific"],
        ["Germline: matched normal", f"> {qc.get('max_normal_alt_reads')} ALT read(s) in the normal → remove",
         "Direct evidence of germline origin"],
        ["Consequence", "Protein-altering only", ", ".join(sorted(PROTEIN_ALTERING)).replace("_", " ")],
    ], [2.4, 3.0, 4.0])
    b.h2("2.3 Stage 2: Entity resolution")
    b.p("Names are resolved deterministically against local reference dictionaries: gene symbols to HGNC, Entrez and "
        "UniProt identifiers; variants to HGVS on the annotated transcript; the cancer type to OncoTree with its "
        f"lineage (this case: “{cancer.get('input') or code}” → {lineage}). An unknown cancer type stops the run. "
        "Resolution is variant-level, not gene-only (Table 6).")
    b.h2("2.4 Stage 3: Variant tiering")
    b.p("Every QC-passed variant is assigned to one tier (Table 2); tiers are checked in order and each variant takes "
        "the first it meets. There is no top-N cut. The scheme follows the logic of the AMP/ASCO/CAP and ESCAT "
        "frameworks.")
    examples = {t: _and([v["notation"] for v in tiered if v["tier"] == t]) or "None in this case" for t in (1, 2, 3)}
    w, bp = t3.get("weights") or {}, t3.get("b_parts") or {}
    b.table("Tier definitions.", ["Tier", "Criteria", "Example (this case)"], [
        [cell("Tier 1: subtype-specific", bold=True), "CIViC level A/B (or OncoKB level 1, 2 or R1) evidence in this "
         "cancer type or a parent type, matched to the exact variant, codon or exon (or, for a truncating variant in a "
         "tumour suppressor, the variant class)", examples[1]],
        [cell("Tier 2: known hotspot or driver", bold=True), "A hotspot in cancerhotspots.org; or a truncating "
         "variant in an IntOGen tumour-suppressor driver of this cancer type; or variant-level evidence in other "
         "cancer types", examples[2]],
        [cell("Tier 3: weighted score", bold=True), "All remaining protein-altering variants, scored with S (below)",
         examples[3]],
    ], [2.2, 4.8, 2.4])
    b.p(run(f"S = {w.get('F')} · F + {w.get('R')} · R + {w.get('B')} · B", italic=True),
        run("    (each component scaled 0–1)"))
    studies = cfg.get("CBIOPORTAL_SUBTYPE_STUDIES", {})
    b.table("Tier 3 score components.", ["Component", "Weight", "Source", "Scaling to 0–1"], [
        [cell("F: functional impact", bold=True), w.get("F"), "AlphaMissense, else REVEL, else CADD",
         "The first available score; CADD PHRED ÷ 40, capped at 1; truncating variants 0.9; no score 0.5"],
        [cell("R: subtype relevance", bold=True), w.get("R"), f"cBioPortal ({studies.get(code, 'subtype study')})",
         f"Gene mutation frequency in the subtype ÷ {_pct(t3.get('r_saturation'))}, capped at 1"],
        [cell("B: biological relevance", bold=True), w.get("B"), "IntOGen driver role; pathway list",
         f"+{bp.get('cancer_role')} driver role; +{bp.get('pathway')} core pathway; +{bp.get('tsg_loss')} truncating "
         f"in a tumour suppressor; capped at 1"],
    ], [2.2, 0.9, 2.7, 3.6])
    b.p(f"Variants with S < {t3.get('min_score')} are labelled low priority and listed with the reason; they are kept "
        "in the report but not used for drug matching. Tier 3 variants reach Track B only, and their drug scores are "
        "multiplied by S (Section 2.8). F, R, B and S are shown for every Tier 3 and low-priority variant.")
    b.h2("2.5 Stage 4: Drug–gene interaction retrieval")
    b.p(f"Every tiered variant was queried against the enabled sources ({_and(on) or 'none'}; not enabled: "
        f"{_and(off) or 'none'}). CIViC (and OncoKB, when enabled) give variant-level response and resistance "
        "evidence; DGIdb and ChEMBL give gene-level links and mechanisms, used for Track B and context only; "
        "PubTator3 gives literature for Track B. Versions and record counts are in Table 7 of Appendix C.")
    b.h2("2.6 Stage 5: Evidence classification")
    b.p("A deterministic tool merges the records per drug, removes duplicates and keeps each record's source "
        "accession (CIViC EID, PMID). Each drug–variant link is then classed as beneficial (response evidence), "
        "potentially harmful (resistance, a drug–drug interaction or a drug warning) or without evidence, and each drug "
        "is placed on a track. Track A (established) requires CIViC level A/B (or OncoKB level 1/2) evidence in this "
        "cancer type for a Tier 1 variant with a variant-specific match. Everything else goes to Track B "
        "(exploratory): CIViC levels C–E, evidence from other cancer types, gene-level DGIdb/ChEMBL links and Tier 3 "
        "variants. Co-occurring variants are checked together, because a drug's value can depend on the combination.")
    b.h2("2.7 Stage 6: Language-model agents")
    agent_lines = []
    if "primary" in b.reports:
        r = b.reports["primary"]
        agent_lines.append(f"Analysis agent ({r['model']}, prompt template v{r.get('template_version') or '?'}, "
                           f"{_temperature(r['model'])}): explains both tracks (Section 4).")
    for role, text in (("literature", "PubTator3 papers for Tier 3 variants and level C–E pairs, re-ranked, with the "
                                      "abstract sentences that answer the question"),
                       ("biology", "pathway and co-mutation context"),
                       ("hypothesis", "testable variant–drug hypotheses for follow-up")):
        if role in b.reports:
            agent_lines.append(f"{AI_ROLES[role]} agent (Track B only): {text} (Appendix A).")
    if "clinical" in b.reports:
        agent_lines.append("Clinical summary agent: page-1 brief results, regimen options (Section 4.1) and "
                           "monitoring (Section 4.2); statements from general clinical knowledge are marked [GK].")
    b.bullets(agent_lines or ["No language-model agent ran for this query."])
    invalid = sum(len(r.get("invalid_citations") or []) for r in b.reports.values())
    flagged = [AI_ROLES.get(k, k) for k, r in b.reports.items() if r.get("status") == "flagged"]
    b.p("Guardrails: every statement must cite a supplied evidence record, be labelled “Hypothesis:” (exploration "
        "agents) or be marked [GK] (clinical summary). A validator rejects citations to evidence that was not supplied "
        f"(this query: {invalid} rejected) and flags reports that break the rules"
        + (f" (flagged: {_and(flagged)})" if flagged else " (none flagged)") + ". Agent output is stored separately "
        "and cannot change any tier, category or score.")
    b.h2("2.8 Stage 7: Scoring and ranking")
    b.p("Each drug receives a rule-based score (Table 4). The score ranks drugs within a track; it is not a probability "
        f"of response. Tracks A and B are ranked separately; the top {cfg['VCF_TOP_PER_TRACK']} per track appear in "
        "this report.")
    b.p(run("Score", bold=True), run(" = evidence level + cancer-type match + variant-match specificity + marketed "
                                     "drug − resistance penalty"))
    lv = " · ".join(f"{k} {v}" for k, v in LEVEL_POINTS.items())
    sp = SPECIFICITY_POINTS
    n_score = b.table("Score components.", ["Component", "Points", "Why it is included"], [
        ["Evidence level (CIViC)", lv, "Stronger evidence ranks higher"],
        ["Cancer-type match", f"+{SUBTYPE_POINTS[3]} same type · +{SUBTYPE_POINTS[2]} related lung cancer · "
         f"+{SUBTYPE_POINTS[1]} other", "Evidence from this cancer type is more relevant"],
        ["Variant-match specificity", f"+{sp['gene']} (gene) … +{sp['exact']} (exact variant)",
         "Exact matches outrank codon, exon or gene-level matches"],
        ["More supporting items", "+2 each, up to +10", "Several independent records support the drug"],
        ["Marketed drug", "+5", "ChEMBL max phase 4 for any indication (not necessarily this cancer)"],
        ["Gene-level link only (Track B)", f"ChEMBL +{GENE_LEVEL_POINTS['ChEMBL']} · DGIdb +{GENE_LEVEL_POINTS['DGIdb']}",
         "A drug–gene link without variant-level evidence"],
        ["Tier 3 variant only (Track B)", "Score × S", "Weak variant evidence scales the score down"],
        ["Literature support (Track B)", f"+{LITERATURE_POINTS} per retrieved paper, up to +{LITERATURE_MAX}",
         "Papers kept by the literature search"],
        ["Strong resistance", f"{STRONG_RESISTANCE_PENALTY}", "A patient variant with level A–C resistance in this or a "
         "related cancer type; the drug is flagged “not recommended”"],
        ["Weaker resistance · reports of no response", f"{WEAK_RESISTANCE_PENALTY} · {NO_BENEFIT_PENALTY} each (up to "
         "−15)", "Preclinical or other-tumour resistance; conflicting reports"],
        ["Withdrawn drug (ChEMBL)", f"{cfg['VCF_WITHDRAWN_PENALTY']}", "Withdrawn from the market somewhere"],
    ], [2.6, 3.0, 3.8])
    examples = [c for c in (top, down[0] if down else None, b_top[0] if b_top else None) if c]
    if examples:
        b.p("Worked examples from this case: " + "; ".join(
            f"{_lc(c['drug'])} = {_sum_text(c)} = {c['score']}"
            + (" (not recommended)" if c.get("strong_resistance") else "") for c in examples) + f" (component values in Figure 2). Ties share a rank. Regimens and combinations "
            "beyond those in CIViC are summarised separately (Section 4.1) and are not scored.")
    b.h2("2.9 Drug safety checks")
    b.p("After ranking, every drug is checked with fixed rules: ChEMBL drug warnings (a withdrawn drug loses points; "
        "boxed warnings are flagged), and drug–drug interactions with the current medications and between the drugs of "
        "each combination regimen. Interactions come from the drug's FDA label (openFDA) and the FDA table of CYP and "
        "transporter inhibitors, inducers and substrates, plus phenoconversion when a co-medication inhibits a "
        "pharmacogene with a CPIC guideline. Interactions are flagged, not scored.")

    # ----- 3 Results -----
    b.h1("3\tResults")
    b.h2("3.1 Quality control")
    removed = []
    for v in audit:
        where = f"{v.get('gene') + ' ' if v.get('gene') else ''}{v.get('protein_change') or ''}".strip() or \
            f"chr{v.get('chrom')}:{v.get('pos')} {v.get('ref')}>{v.get('alt')}"
        reads = f"DP {v['depth']}, {v.get('alt_reads')} ALT reads, VAF {_vaf(v.get('vaf'))}" \
            if v.get("depth") is not None else "no read counts"
        removed.append(f"{where} ({reads}): {v.get('reason')}")
    b.p(f"Of {total} records in the file, {counts.get('qc_passed', 0)} protein-altering somatic variants passed all "
        f"filters (Table {b.tables + 1})." + (f" Removed: {'; '.join(removed[:6])}" + ("; …" if len(removed) > 6 else "")
                                              + "." if removed else ""))
    steps, remaining = [], total
    unmatched = list(audit)
    for label, pattern in QC_STEPS:
        hit = [v for v in unmatched if re.search(pattern, v.get("reason") or "", re.I)]
        unmatched = [v for v in unmatched if v not in hit]
        remaining -= len(hit)
        if hit or label in ("FILTER ≠ PASS", "Read depth below the minimum", "VAF below the minimum",
                            "Germline: population frequency (gnomAD)", "Germline: present in the matched normal",
                            "Not protein-altering"):
            steps.append([label, str(len(hit)), str(remaining)])
    if unmatched:
        remaining -= len(unmatched)
        steps.append(["Other reasons", str(len(unmatched)), str(remaining)])
    b.table("Variants removed and remaining at each QC step.", ["QC step", "Removed", "Remaining"],
            [[cell("Records in the file", bold=True), "–", str(total)]] + steps, [6.0, 1.7, 1.7])

    b.h2("3.2 Candidate variants and tiers")
    rows6 = []
    for v in passed:
        hgnc = (v.get("resolved") or {}).get("hgnc_id")
        transcript = (v.get("hgvsc") or "").split(":")[0] or v.get("transcript") or ""
        change = " ".join(filter(None, [(v.get("hgvsc") or "").split(":")[-1] or None,
                                        f"p.{v['protein_change']}" if v.get("protein_change") else None]))
        tier = str(v["tier"]) if v.get("tier") else "3 (low priority)"
        rows6.append([[run(v.get("gene") or "–", italic=True)] + ([run(f"\n{hgnc}")] if hgnc else []),
                      f"{transcript}\n{change}".strip(),
                      f"{_vaf(v.get('vaf'))} · {v.get('depth') if v.get('depth') is not None else '–'}", tier,
                      v.get("tier_reason") or v.get("reason"), _role(v)])
    n6 = b.table("QC-passed variants: identifiers, tiers and reasons.",
                 ["Gene (ID)", "Variant (transcript)", "VAF · depth", "Tier", "Reason for tier", "Driver role (IntOGen)"],
                 rows6, [1.4, 2.2, 1.2, 0.9, 2.6, 1.6])
    scored = [v for v in passed if v.get("tier3")]
    if scored:
        b.p("Tier 3 and low-priority components: " + "; ".join(
            f"{v.get('notation') or v.get('gene')} (F {v['tier3']['F']} from {v['tier3']['F_source']}, R {v['tier3']['R']}, "
            f"B {v['tier3']['B']}; S = {v['tier3']['S']}{' < ' + str(t3.get('min_score')) + ', low priority' if not v.get('tier') else ''})"
            for v in scored) + ".")
    b.p(f"Variants are listed in Table {n6} with their tier and the rule that set it.")

    b.h2("3.3 Variant–drug evidence overview")
    # Columns: the best established drugs, the down-ranked ones (so resistance shows), resistance-only drugs, then the
    # top exploratory ones
    cols = [c for c in a_top if not c.get("strong_resistance")][:4] \
        + [c for c in established if c.get("strong_resistance")][:3] + resist_only[:2] + b_top[:2]
    track_of = {id(c): "A" if c in established else "B" if c in exploratory else "R" for c in cols}
    variants_by_gene = {}
    for v in tiered:
        variants_by_gene.setdefault(v.get("gene"), []).append(v["id"])
    if cols and passed:
        maps = [_evidence_cells(c, items, variants_by_gene) for c in cols]
        rows_f1 = []
        for v in passed:
            label = f"{v.get('notation') or v.get('gene')} ({'Tier ' + str(v['tier']) if v.get('tier') else 'low priority'})"
            row = [cell(label, bold=True)]
            for m in maps:
                text, fill, color = m.get(v["id"], ("", NONE_FILL, None))
                row.append(cell(text, fill=fill, color=color, center=True, bold=bool(text)))
            rows_f1.append(row)
        headers = ["Variant"] + [f"{c['drug']} ({track_of[id(c)]})" for c in cols]
        b.figure_table("Heat map of variant × drug evidence: the best established drugs, the down-ranked ones, "
                       "drugs with resistance evidence only (R) and the top exploratory drugs (A = Track A, B = Track B). "
                       "Blue = CIViC response evidence (darker = stronger level, A–E); red = resistance (level in "
                       "brackets); pale = gene-level link only; grey = no evidence.",
                       headers, rows_f1, [2.4] + [1] * len(cols))
        b.p(f"Figure {b.figures} shows the evidence retrieved for each variant and the top drugs.")
    else:
        b.p("No drug evidence was retrieved for the variants of this case.")

    b.h2("3.4 Drug interactions: beneficial, harmful and no evidence")
    b.h3("3.4.1 Beneficial drug interactions")
    rows7 = []
    for c in a_top + b_top:
        how = c.get("mechanism") or "; ".join(sorted({f"{g['label'] or 'interacts with'} {g['gene']}"
                                                      for g in c.get("gene_links") or []})) or "Response evidence"
        if c.get("strong_resistance"):
            how += "; not recommended because of resistance (3.4.2)"
        level = (f"CIViC · {c['best']['level']}, {c['best']['disease_match']}" if c.get("best")
                 else f"OncoKB · {c['oncokb_level'].replace('LEVEL_', '')}" if c.get("oncokb_level")
                 else " / ".join(sorted({g["source"] for g in c.get("gene_links") or []})) + " · gene level")
        rows7.append([cell(c["drug"], bold=True), ", ".join(c.get("variants") or []) or "–", how,
                      [run(level + " "), *b.refs.cite(c.get("evidence_ids")[:4])], "A" if c in a_top else "B",
                      f"{c['score']}" + (" (not rec.)" if c.get("strong_resistance") else "")])
    if rows7:
        b.table("Beneficial drug interactions (top drugs of each track).",
                ["Drug", "Gene / variant", "How it is beneficial", "Source · level", "Track", "Score"],
                rows7, [1.7, 1.7, 2.8, 2.0, 0.6, 0.9])
    else:
        b.p("No drug has response evidence for the variants of this case.")
    b.h3("3.4.2 Potentially harmful drug interactions")
    rows8 = []
    for c in [c for c in established + exploratory if c.get("resistance_notes")][:15]:
        eids = [e for e in c.get("evidence_ids") or [] if (items.get(e) or {}).get("significance") in RESISTANT]
        rows8.append([cell(c["drug"], bold=True), c.get("strong_resistance_text") or "patient variant",
                      cell("Resistance", bold=True), "; ".join(c["resistance_notes"][:3]),
                      b.refs.cell(eids[:4]), str(STRONG_RESISTANCE_PENALTY) if c.get("strong_resistance")
                      else str(WEAK_RESISTANCE_PENALTY)])
    for c in resist_only:
        rows8.append([cell(c["drug"], bold=True), c.get("strong_resistance_text") or "patient variant",
                      cell("Resistance (no response evidence)", bold=True), "; ".join(c.get("resistance_notes") or []),
                      b.refs.cell(c.get("evidence_ids")[:4]), "Not ranked"])
    for a in alerts:
        rows8.append([cell(a["drug"], bold=True), f"with {a['other']} ({a['context']})",
                      cell(f"Drug–drug interaction ({a['severity']})", bold=True),
                      a["summary"] + (f" Label: “{a['label_text'][:220]}”" if a.get("label_text") else ""),
                      b.refs.cell([a.get("evidence_id")]), "Flagged, not scored"])
    for c in warned:
        for w_text in c["safety"]["warnings"]:
            rows8.append([cell(c["drug"], bold=True), "–", cell("Drug warning (ChEMBL)", bold=True), w_text, None,
                          str(cfg["VCF_WITHDRAWN_PENALTY"]) if w_text.startswith("Withdrawn") else "Flagged, not scored"])
    if rows8:
        b.table("Potentially harmful drug interactions.",
                ["Drug", "Gene / variant", "Type of concern", "How it is harmful", "Source", "Effect on score"],
                rows8, [1.6, 1.5, 1.7, 3.2, 0.9, 1.0])
        if not_rec:
            b.p(run("Not recommended: ", bold=True), run(
                f"{_and(sorted(set(not_rec)))}. These drugs carry resistance evidence from the patient's own variants; "
                "they stay on their track only as down-ranked, flagged options."))
    else:
        b.p("No resistance, drug–drug interaction or drug warning was found for the drugs of this case.")
    b.h3("3.4.3 Genes with no drug evidence")
    linked = {vid for c in established + exploratory for vid in (c.get("variant_ids") or [])}
    rows9 = []
    for v in passed:
        if v.get("id") in linked and v.get("tier"):
            continue
        gene_links = sorted({c["drug"] for c in exploratory for g in c.get("gene_links") or [] if g["gene"] == v.get("gene")})
        shows = ("Low priority (S below the threshold): kept, not used for drug matching" if not v.get("tier")
                 else f"Gene-level links only ({len(gene_links)} drug(s), Track B)" if gene_links
                 else "No drug evidence retrieved")
        rows9.append([v.get("notation") or v.get("gene"), str(v["tier"]) if v.get("tier") else "3 (low)", "No", shows])
    if rows9:
        b.table("Variants with no variant-level drug evidence.", ["Gene / variant", "Tier", "Approved targeted drug?",
                                                                   "What the databases show"], rows9, [2.2, 1.0, 1.6, 4.6])
    else:
        b.p("Every tiered variant has variant-level drug evidence.")
    if (ev.get("source_status") or {}).get("oncokb", {}).get("state") == "disabled":
        b.p("OncoKB was not enabled, so absence of evidence may reflect a coverage gap rather than a true absence.")

    b.h2("3.5 Ranked candidates: established evidence (Track A)")
    if a_top:
        rows10 = []
        for c in a_top:
            neg = list(c.get("resistance_notes") or [])[:2] + list(c.get("conflicts") or [])
            neg += [f"{a['severity']} interaction with {a['other']}" for a in (c.get("safety") or {}).get("interactions") or []]
            neg += (c.get("safety") or {}).get("warnings") or []
            rows10.append([str(c["rank"]), cell(c["drug"], bold=True), ", ".join(c.get("variants") or []),
                           c["track_reason"].split("; down-ranked")[0],
                           ("Not recommended: " if c.get("strong_resistance") else "") + ("; ".join(neg) or "None found"),
                           f"{c['score']}" + (" (not rec.)" if c.get("strong_resistance") else "")])
        n10 = b.table("Ranked established candidates (Track A).", ["Rank", "Drug", "Variants", "Positive interaction",
                                                                   "Potentially negative interaction", "Score"],
                      rows10, [0.6, 1.6, 1.6, 2.6, 2.6, 0.9])
    else:
        n10 = None
        b.p((ev.get("outcome") or {}).get("no_targeted_therapy") or "No drug reached the established track.")
    fig_rows = [c for c in a_top if not c.get("strong_resistance")][:5] \
        + [c for c in established if c.get("strong_resistance")][:3] + b_top[:3]
    if fig_rows:
        cols2 = [("level", "Evidence level"), ("cancer", "Cancer type"), ("match", "Variant match"),
                 ("approved", "Marketed"), ("other", "Other"), ("penalty", "Penalties")]
        rows_f2 = []
        for c in fig_rows:
            sums = {k: 0 for k, _ in cols2}
            for p in c.get("score_parts") or []:
                sums[_part_column(p["label"], p["points"])] += p["points"]
            row = [cell(f"{c['drug']} ({'A' if c in established else 'B'})", bold=True)]
            for k, _ in cols2:
                fill, color = _points_fill(sums[k])
                row.append(cell(f"{sums[k]:+d}" if sums[k] else "0", fill=fill, color=color, center=True))
            fill, color = (RESIST_FILL, "FFFFFF") if c.get("strong_resistance") else _points_fill(max(c["score"], 1))
            row.append(cell(f"{c['score']}" + (" not rec." if c.get("strong_resistance") else ""), fill=fill,
                            color=color, bold=True, center=True))
            rows_f2.append(row)
        b.figure_table("Heat map of score components per drug (A = Track A, B = Track B). Blue = points added (darker "
                       "= more); red = penalties; the last column is the total (red = not recommended). “Other” "
                       "holds supporting items, literature, gene-level links and the Tier 3 × S scaling.",
                       ["Drug", *[label for _, label in cols2], "Total"], rows_f2, [2.4, 1, 1, 1, 1, 1, 1, 1.1])
        b.p(f"Figure {b.figures} shows how each score is built" + (f"; Table {n10} lists the established drugs" if n10 else "")
            + ".")

    b.h2("3.6 Ranked candidates: exploratory hypotheses (Track B)")
    b.add(callout(run("Exploratory — not for treatment decisions. ", bold=True),
                  run("These are hypotheses for research or clinical-trial screening only.")), para("Normal"))
    if b_top:
        rows11 = []
        for c in b_top:
            concerns = ([] if c.get("approved") else ["not a marketed drug (ChEMBL)"]) + list(c.get("resistance_notes") or [])[:2]
            concerns += (c.get("safety") or {}).get("warnings") or []
            breakdown = _sum_text(c)
            rows11.append([str(c["rank"]), cell(c["drug"], bold=True), ", ".join(c.get("variants") or []) or "–",
                           c["track_reason"], "; ".join(concerns) or "–",
                           [run(f"{c['score']} ({breakdown}) "), *b.refs.cite(c.get("evidence_ids")[:3])]])
        b.table("Ranked exploratory hypotheses (Track B).", ["Rank", "Drug", "Variant", "Possible benefit", "Concerns",
                                                             "Score"], rows11, [0.6, 1.6, 1.4, 2.6, 2.0, 1.6])
    else:
        b.p("No exploratory candidate was found.")

    b.h2("3.7 Co-mutations without a direct drug")
    co_rows = [[cell(v.get("gene"), bold=True), v.get("protein_change") or v.get("notation"), "No",
                "; ".join(filter(None, [v.get("tier_reason"), (", ".join(v.get("pathways") or []) + " pathway")
                                        if v.get("pathways") else None]))] for v in ev.get("co_mutations") or []]
    co_rows += [[cell(v.get("gene"), bold=True), v.get("protein_change") or "–", "No",
                 f"Low priority ({v.get('reason')})"] for v in low]
    if ev.get("co_mutation_patterns"):
        co_rows.append([cell("Pattern", bold=True), "; ".join(ev["co_mutation_patterns"]), "–",
                        "Known co-mutation pattern(s) present in this sample"])
    if co_rows:
        b.table("Co-mutations without a direct drug.", ["Gene", "Variant", "Direct drug?", "Interpretation"],
                co_rows, [1.2, 1.8, 1.1, 5.3])
    else:
        b.p("Every tiered variant has variant-level drug evidence.")
    b.network_figure("Variant–drug network: variants, genes and candidate drugs; line style shows response, "
                     "resistance or a gene-level link.")

    # ----- 4 Interpretation -----
    b.h1("4\tInterpretation")
    if not b.llm("primary", "Drafted by the Analysis agent ({model}) from Section 3; every statement is traced to a "
                            "supplied evidence record or labelled as a hypothesis. It has not been edited for content."):
        b.p("No Analysis agent report is available for this query.")
    b.h2("4.1 Treatment options for this patient")
    options = sec("Treatment options")
    if options:
        b.p("This table turns the drug ranking into regimens a treating physician would weigh. It was drafted by the "
            "Clinical summary agent: regimens and trials marked [GK] come from general clinical knowledge, not from "
            "the retrieved evidence or the pipeline score, and must be checked against current guidelines. The final "
            "choice depends on information not supplied here (performance status, CNS disease, comorbidities, "
            "patient preference and drug access).")
        headers, rows13 = md_table([line for line in options.splitlines() if line.strip().startswith("|")], b.refs)
        if headers:
            b.table("First-line regimen options for this patient (drafted by the Clinical summary agent; [GK] = "
                    "general clinical knowledge).", headers, rows13, [2.0, 1.8, 2.0, 2.0, 1.4])
        rest = "\n".join(line for line in options.splitlines() if not line.strip().startswith("|")).strip()
        if rest:
            b.add(*markdown_paragraphs(rest, b.refs))
    else:
        b.p("No regimen table was generated for this query (the Clinical summary agent did not run).")
    b.h2("4.2 Monitoring and next steps")
    monitoring = sec("Monitoring and next steps")
    if monitoring:
        b.add(para("AINote", "Drafted by the Clinical summary agent; [GK] = general clinical knowledge."))
        b.add(*markdown_paragraphs(monitoring, b.refs, style="Normal"))
    else:
        b.p("Not generated for this query.")

    # ----- 5 Limitations -----
    b.h1("5\tLimitations and data gaps")
    lims = [[run("Synthetic test data; ", bold=True), run("not for clinical use. Variant counts, VAFs and depths are "
                                                          "taken from the input file.")]]
    lims += [run(t) for t in ev.get("limitations") or []]
    lims += [[run("Clinical context ", bold=True), run("is used only by the language-model agents and in this report; "
                                                       "it does not affect QC, tiers or scores.")],
             [run("Regimens, trials and monitoring ", bold=True), run("in Sections 4.1–4.2 and on page 1 are drafted by "
                                                                      "a language model; statements marked [GK] are not "
                                                                      "backed by the retrieved evidence.")],
             [run("Scores ", bold=True), run("are rule-based ranks, not probabilities of response. Point values and "
                                             "Tier 3 weights are design choices, not fitted to outcomes.")],
             [run("Prototype: ", bold=True), run("lung cancer only (LUAD, LUSC, NSCLC, SCLC); not a validated "
                                                 "diagnostic test. Clinical-trial matching is not implemented.")]]
    b.bullets(lims)

    # ----- 6 Conclusion -----
    b.h1("6\tConclusion")
    concl = [f"For this {cancer_name.lower()} case, the pipeline "
             + (f"ranked {_lc(top['drug'])} first on the established track (score {top['score']})"
                if top else "found no established targeted therapy") + "."]
    if not_rec:
        concl.append(f"{_and(sorted({_lc(d) for d in not_rec}))} {'are' if len(set(not_rec)) > 1 else 'is'} flagged "
                     "as not recommended because of resistance from the patient's variants.")
    if b_top:
        concl.append("Exploratory ideas stay in a clearly labelled track.")
    if alerts:
        concl.append(f"{len(alerts)} drug–drug interaction alert(s) need review before prescribing.")
    concl.append("Every drug can be traced to a variant, an evidence source and a score breakdown, and the page-1 "
                 "summary gives the treating physician the options, cautions and next steps at a glance.")
    b.p(" ".join(concl))

    # ----- appendices (built first so that their citations are numbered too; the references go before them) -----
    mark = len(b.body)
    b.h1("Appendix A: Exploration agents (Track B)")
    shown = False
    for role in ("literature", "biology", "hypothesis"):
        if role in b.reports:
            b.h2(f"A.{['literature', 'biology', 'hypothesis'].index(role) + 1} {AI_ROLES[role]}")
            shown = b.llm(role, "Written by the " + AI_ROLES[role] + " agent ({model}) on Track B only; reproduced "
                                "without editing, apart from citation numbers.") or shown
    if not shown:
        b.p("The exploration agents did not run for this query.")
    b.h1("Appendix B: Variants excluded before tiering")
    if audit:
        steps_of = {"qc": "Quality filter", "germline": "Germline", "consequence": "Consequence"}
        b.table("Variants removed by quality control, germline filtering or consequence, with the reason.",
                ["Step", "Variant", "Position", "ALT / depth", "Reason"],
                [[steps_of.get(v.get("dropped_at"), v.get("dropped_at")),
                  f"{v['gene']} {v.get('protein_change') or ''}".strip() if v.get("gene") else None,
                  f"chr{v.get('chrom')}:{v.get('pos')} {v.get('ref')}>{v.get('alt')}",
                  f"{v.get('alt_reads')}/{v.get('depth')}" if v.get("depth") is not None else None, v.get("reason")]
                 for v in audit], [1.3, 1.5, 2.4, 1.1, 3.1])
    else:
        b.p("No variant was removed.")
    b.h1("Appendix C: Data sources and provenance")
    b.sources_table(["vep", "civic", "oncokb", "dgidb", "chembl", "pubtator", "medcpt", "openfda", "pbs"])
    b.provenance(None, [
        ["Input file", f"{sample} ({(inp.get('format') or 'vcf').upper()}, {inp.get('assembly') or 'GRCh38'})"],
        ["Cancer type", f"{cancer_name} (OncoTree {code}; lineage {lineage})"],
        ["Clinical context", clinical_summary(clinical) or "not provided"],
        ["Current medications", ", ".join(meds) or "none given"],
        *([["Follow-up question", q["followup_question"]]] if q.get("followup_question") else []),
    ])
    appendices = b.body[mark:]
    del b.body[mark:]
    b.references(VCF_BIB)
    b.add(*appendices)
    genes = sorted({v.get("gene") for v in tiered if v.get("gene")})
    return (f"{sample}: candidate drug report", f"{genes[0] if genes else 'VCF'}_{code}")



# ---------- Gene → drugs / drug → genes ----------

def build_dgi(b):
    q, ev = b.q, b.ev
    is_drug = q.get("entity_type") == "drug"
    norm = (ev.get("normalisation") or [{}])[0]
    items = ev.get("items") or []
    profile = next((i for i in items if i.get("kind") == "profile"), {})
    name = norm.get("name") or q["entities"][0]
    name = _name(name) if is_drug else name  # gene symbols stay as HGNC writes them
    full = profile.get("full_name") if not is_drug else None
    subject = f"{name} ({full})" if full and full.upper() != name.upper() else name
    rows = ev.get("table") or []
    grades = {g: sum(1 for r in rows if r.get("grade") == g) for g in GRADE_ORDER}
    guidelines = [i for i in items if i.get("kind") == "guideline"]
    annotations = [i for i in items if i.get("kind") == "variant_annotation"]
    pbs = ev.get("australian_availability") or []
    safety = ev.get("safety") or {}
    alerts = safety.get("interactions") or []
    meds = [m["name"] for m in safety.get("medications") or []]
    geno = ev.get("genotypes") or {}
    geno_entered = geno.get("entered") or []
    by_id = {i.get("id"): i for i in items}
    geno_recs = [by_id[e] for e in geno.get("recommendations") or [] if e in by_id]
    geno_brief = []  # one per drug (the strongest; recommendations are sorted Strong first) for page 1
    for r in geno_recs:
        if r["drug"] not in {x["drug"] for x in geno_brief} and r.get("classification") != "No Recommendation":
            geno_brief.append(r)
    rel = q.get("relation_types") or []
    all_types = set(rel) >= set(DEFAULT_RELATION_TYPES)
    rel_text = "all relationship types" if all_types else _and([x.lower() for x in labels(rel)])
    partner, partner_one = ("genes", "gene") if is_drug else ("drugs", "drug")
    other = (lambda r: r["gene"]) if is_drug else (lambda r: _name(r["drug"]))
    cpic_ab = sorted({(g.get("gene") if is_drug else g.get("drug") or "").strip() for g in guidelines
                      if str(g.get("cpic_level") or "").startswith(("A", "B"))} - {""})
    top_annotations = [a for a in annotations if a.get("level") in ("1A", "1B")]
    strongest = [r for r in rows if r.get("grade") in ("strong", "moderate")][:5]
    warned = _dgi_warned(q, rows, ev)
    on, off = b.sources()
    primary = b.reports.get("primary")

    # ----- page 1 -----
    if is_drug:
        profile_text = "; ".join(filter(None, [
            profile.get("molecule_type"), profile.get("first_approval") and f"first approved {profile['first_approval']}",
            profile.get("max_phase") is not None and f"max clinical phase {profile['max_phase']:g}",
            profile.get("trade_names") and "trade names " + ", ".join(profile["trade_names"][:4])])) or "–"
    else:
        protein = profile.get("protein") or {}
        profile_text = "; ".join(filter(None, [full, profile.get("location") and f"location {profile['location']}",
                                               protein.get("protein_name")])) or "–"
    b.page_one(
        "Drug–Gene Interaction Explorer: " + ("Drug → Genes" if is_drug else "Gene → Drugs") + " Report",
        "This report summarises database and literature evidence for research and teaching. It is not a treatment "
        "recommendation. Grades are assigned by fixed rules; the language model only explains the evidence and cannot "
        "change any grade.",
        [("Query", f"“{norm.get('input') or q['entities'][0]}” ({'drug' if is_drug else 'gene'})",
          "Report ID / date", f"{b.number} · {_date(datetime.now(timezone.utc))}"),
         ("Resolved as", f"{name} ({norm.get('id') or 'unresolved'}; {norm.get('matched_by') or 'searched as typed'})",
          "Relationship types", rel_text[:1].upper() + rel_text[1:]),
         ("Profile", profile_text, "Current medications", ", ".join(meds) or "None given"),
         *([("Patient genotypes", "; ".join(f"{x['input']} → {x.get('phenotype') or 'not interpreted'}"
                                            for x in geno_entered), "Genotype source", "Entered by the user")]
           if geno_entered else []),
         ("Sources enabled", ", ".join(on) or "–", "Not enabled", ", ".join(off) or "–"),
         ("Reviewed / authorised by", "______________________", "Signature / date", "______________________")])
    b.brief_results([
        ("Strongest interactions", "; ".join(f"{other(r)}: {r.get('grade_label')} ({r.get('grade_reason')})"
                                             for r in strongest) or "No pair graded strong or moderate."),
        ("Prescribing guidance", f"CPIC level A/B guidelines for {_and(cpic_ab)}." if cpic_ab
         else "No CPIC level A/B guideline."),
        *([("Patient genotype", _inline("; ".join(
            f"{_name(r['drug'])}: {r.get('short_recommendation')} ({r['phenotype']}; CPIC "
            f"{(r.get('classification') or '').lower()}"
            f"{', ' + _pop(r) if _pop(r) != 'All patients' else ''}) [{r['id']}]"
            for r in geno_brief[:4]) + (f"; all {len(geno_recs)} recommendations in Section 3.4."
                                        if len(geno_recs) > len(geno_brief[:4]) else ""),
            b.refs) if geno_brief else "No CPIC recommendation applies to the drugs found for the genotypes entered.")]
          if geno_entered else []),
        ("Pharmacogenomic annotations", f"{len(annotations)} PharmGKB clinical annotations ({len(top_annotations)} at "
                                        f"level 1A/1B)." if annotations else "None retrieved."),
        ("Drug interactions and warnings",
         "; ".join(filter(None, [
             (f"{len(alerts)} drug–drug interaction alert(s) with the current medications (Section 3.5)" if alerts
              else ("No interaction found with the current medications" if meds else "No current medications given")),
             f"{len(warned)} drug(s) with a ChEMBL warning" if warned else None])) + "."),
        ("Australian availability", f"{sum(1 for a in pbs if a.get('pbs_listed'))} of {len(pbs)} drugs checked are "
                                    "PBS-listed (Section 3.6)." if pbs else "PBS not checked."),
        ("Not covered by this search", ("Dose calculation" if geno_entered else "Dose calculation, patient genotypes")
                                       + " and pharmacodynamic interactions; live sources reflect the time of the search."),
    ], note="All rows are rule-based summaries of Section 3.")

    # ----- abstract -----
    used = [SOURCE_NAMES.get(k, k) for k in ("dgidb", "chembl", "cpic", "pharmgkb", "pubtator", "uniprot",
                                             "fda_ddi", "pbs") if (ev.get("source_status") or {}).get(k, {}).get("state") == "ok"]
    results = [f"{len(rows)} drug–gene pairs were identified: {grades['strong']} strong, {grades['moderate']} "
               f"moderate, {grades['supportive']} supportive and {grades['limited']} limited."]
    if cpic_ab:
        results.append(f"CPIC level A/B prescribing guidelines exist for {_and(cpic_ab)}.")
    if annotations:
        results.append(f"{len(annotations)} PharmGKB clinical annotations were retrieved ({len(top_annotations)} at "
                       f"level 1A/1B).")
    if alerts:
        results.append(f"{len(alerts)} drug–drug interaction alert(s) were flagged against the current medications.")
    strong_names = [other(r) for r in rows if r.get("grade") == "strong"]
    conclusion = (f"Genotype-guided prescribing is supported by guideline-level evidence for {_and(cpic_ab)}; the "
                  "remaining interactions rest on lower-level or database-only evidence." if cpic_ab else
                  f"Strong evidence supports the interaction with {_and(strong_names[:5])}; the rest is weaker."
                  if strong_names else "No pair reached the strong grade; the interactions found rest on moderate or "
                  "lower evidence." if rows else f"No drug–gene interaction was found for {name} in the sources searched.")
    b.abstract([
        ("Objective", f"To identify the {partner} that interact with {subject} and to summarise the supporting evidence, "
                      f"considering {rel_text}."),
        ("Methods", f"Evidence was retrieved from {len(used)} sources ({_and(used)}), merged per drug–gene pair and "
                    "graded with fixed rules; drugs were checked for warnings and, when current medications were "
                    "given, for drug–drug interactions."
                    + (f" A narrative interpretation was written by a large language model ({primary['model']}) "
                       "constrained to cite the retrieved evidence." if primary else "")),
        ("Results", " ".join(results)),
        ("Conclusion", conclusion),
    ], [name, "pharmacogenomics", "drug–gene interaction"] + (["CPIC"] if guidelines else [])
       + (["drug–drug interaction"] if alerts else []) + ["evidence grading"])

    # ----- 1 Introduction -----
    b.h1("1\tIntroduction")
    b.p("The Drug–Gene Interaction Explorer (Pharmagenix) collects the published evidence on how drugs and genes "
        "interact (inhibition, induction, binding, metabolism and genotype-specific response) from curated databases "
        "and live literature and interaction services, and makes its strength explicit. This report answers a query "
        f"for the {partner} that interact with the {'drug' if is_drug else 'gene'} {name}. The design is hybrid: "
        "retrieval, merging, grading and safety checks are deterministic and reproducible; a language model only "
        "explains the graded evidence. The report does not make treatment recommendations.")

    # ----- 2 Methodology -----
    b.h1("2\tMethodology")
    b.h2("2.1 Input specification")
    b.p(f"The input is one {'drug (generic or brand name)' if is_drug else 'gene (symbol, alias or previous symbol)'}, "
        f"the relationship types of interest ({rel_text}) and, optionally, the patient's current medications.")
    b.h2("2.2 Entity resolution")
    if norm:
        b.p(f"The input “{norm.get('input') or q['entities'][0]}” was resolved to {name} ({norm.get('id')}) by "
            f"{norm.get('matched_by') or 'exact match'}, against the local {'ChEMBL drug names and synonyms' if is_drug else 'HGNC complete set'}. "
            "Unknown names are searched as typed only after the user confirms.")
    else:
        b.p(f"The input “{q['entities'][0]}” was used as entered.")
    b.h2("2.3 Data sources")
    n_src = b.sources_table(["dgidb", "chembl", "cpic", "pharmgkb", "pubtator", "uniprot", "drugbank", "fda_ddi",
                             "openfda", "pbs"], note="Records: number retrieved for this query (after relationship-type "
                                                     "filtering).")
    b.p(f"Sources, versions and record counts are in Table {n_src}. Local datasets were imported in full and versioned; "
        "the others were queried live at the time of the search.")
    b.h2("2.4 Evidence integration and grading")
    n_grade = b.table("Evidence grades, assigned by fixed rules to each drug–gene pair (the strongest evidence decides).",
                      ["Grade", "Criteria"], [
                          [cell("Strong", fill=GRADE_FILL["strong"], color="FFFFFF", bold=True),
                           "CPIC level A or B guideline, or PharmGKB clinical annotation level 1A/1B"],
                          [cell("Moderate", fill=GRADE_FILL["moderate"], color="FFFFFF", bold=True),
                           "PharmGKB level 2A/2B; curated ChEMBL mechanism of action; FDA clinical classification as a "
                           "strong / moderate CYP inhibitor or inducer or a sensitive substrate; or ≥3 DGIdb sources "
                           "with literature"],
                          [cell("Supportive", fill=GRADE_FILL["supportive"], bold=True),
                           "Lower-level pharmacogenomic evidence (CPIC C/D, PharmGKB 3), a weak or transporter role in "
                           "the FDA table, or ≥2 DGIdb sources or literature"],
                          [cell("Limited", fill=GRADE_FILL["limited"], bold=True),
                           "A single database record without literature"]], [1.6, 7.8])
    b.p(f"Records describing the same pair were merged and graded as in Table {n_grade}. Opposite directions of action "
        "for the same pair are flagged as conflicts. Grades are not assigned or altered by the language model.")
    b.h2("2.5 Drug safety checks")
    b.p("Every drug listed is checked for ChEMBL drug warnings (withdrawals, boxed warnings). When current medications "
        "are given, the best-supported drugs are checked against them with four rules: the drug's FDA label (openFDA) "
        "covers a class the medication belongs to in the FDA CYP / transporter table; the label names the medication; "
        "the FDA table pairs a strong or moderate inhibitor or inducer with a sensitive substrate; and phenoconversion, "
        "when the medication inhibits a pharmacogene the drug has a CPIC guideline for. Interactions are flagged, not "
        "graded.")
    b.h2("2.6 Narrative generation")
    if primary:
        b.p(f"The interpretation in Section 4 was generated by {primary['model']}"
            f"{', prompt template v' + str(primary['template_version']) if primary.get('template_version') else ''}, "
            f"{_temperature(primary['model'])}. The model received the graded pairs and the evidence records only, was "
            "instructed to cite an evidence identifier for every factual sentence, and its output was checked "
            f"automatically: {len(primary.get('citations') or [])} citations, "
            f"{len(primary.get('invalid_citations') or [])} to evidence not supplied. Citations are renumbered here to "
            "match the reference list.")
    else:
        b.p("No language-model interpretation was produced for this query.")

    # ----- 3 Results -----
    b.h1("3\tResults")
    b.h2("3.1 Overview")
    shown, total = b.figure_note.get("shown"), b.figure_note.get("total")
    fig = b.network_figure(f"Interaction network of {name}. Each line joins the queried {'drug' if is_drug else 'gene'} "
                           f"to an interacting {partner_one}; line style and arrowhead show the relationship type."
                           + (f" Showing {shown} of {total} relationships." if shown and total and shown < total else ""))
    b.p(f"In total {len(rows)} pairs were identified{f' (Figure {fig})' if fig else ''}: {grades['strong']} strong, "
        f"{grades['moderate']} moderate, {grades['supportive']} supportive and {grades['limited']} limited.")

    b.h2("3.2 Evidence overview")
    heat = [r for r in rows if r.get("grade") != "limited"][:14]
    if heat:
        def level_cell(value, darker):
            if not value:
                return cell("", fill=NONE_FILL)
            return cell(value, fill=LEVEL_FILL["A" if darker else "C"], color="FFFFFF" if darker else None,
                        center=True, bold=True)

        heat_rows = []
        for r in heat:
            fda = (r.get("fda_role") or {}).get("label")
            heat_rows.append([
                cell(other(r), bold=True),
                level_cell(r.get("cpic_level"), str(r.get("cpic_level") or "").startswith(("A", "B"))),
                level_cell(r.get("pgkb_level"), r.get("pgkb_level") in ("1A", "1B")),
                level_cell("mechanism" if "ChEMBL" in (r.get("sources") or []) else None, True),
                level_cell(fda, (r.get("fda_role") or {}).get("strength") in ("strong", "sensitive")),
                level_cell(str(len(r.get("databases") or [])) if r.get("databases") else None,
                           len(r.get("databases") or []) >= 3),
                level_cell(str(len(r.get("pmids") or [])) if r.get("pmids") else None, len(r.get("pmids") or []) >= 3),
                cell(r.get("grade_label"), fill=GRADE_FILL[r["grade"]], center=True, bold=True,
                     color="FFFFFF" if r["grade"] in ("strong", "moderate") else None),
            ])
        b.figure_table(f"Heat map of the evidence behind each {partner_one} (pairs graded above limited, up to 14). "
                       "Columns: CPIC level, PharmGKB level, curated ChEMBL mechanism, FDA CYP / transporter role, "
                       "number of DGIdb source databases and of PMIDs; darker = stronger. The last column is the grade.",
                       [partner_one.capitalize(), "CPIC", "PharmGKB", "ChEMBL", "FDA role", "DGIdb", "PMIDs", "Grade"],
                       heat_rows, [1.8, 0.8, 1.0, 1.1, 1.9, 0.8, 0.8, 1.1])

    b.h2("3.3 Drug–gene interactions")
    supported = [r for r in rows if r.get("grade") != "limited"][:40]
    omitted = len(rows) - len(supported)
    if supported:
        n = b.table("Drug–gene pairs graded strong, moderate or supportive.",
                    [partner_one.capitalize(), "Relationship", "Grade", "Highest level", "Sources", "Refs"],
                    [[cell(other(r), bold=True), r.get("relation") or "unspecified", r.get("grade_label") or r.get("grade"),
                      r.get("level") or "–", ", ".join(r.get("sources") or []), b.refs.cell(r.get("evidence_ids"))]
                     for r in supported], [1.9, 2.4, 1.1, 2.0, 1.8, 1.0],
                    note=f"{omitted} further pairs (graded limited, or beyond the first 40) are omitted." if omitted else None)
        b.p(f"The supported pairs are listed in Table {n}.")
    else:
        b.p("No pair was graded above limited.")

    b.h2("3.4 Pharmacogenomic evidence")
    if geno_entered:
        b.p("Patient genotypes entered: " + "; ".join(
            f"{x['input']} → {x.get('phenotype') or 'not interpreted (' + (x.get('note') or x['status']) + ')'}"
            + "".join(f" ({n})" for n in x.get("phenoconversion") or []) for x in geno_entered) + ".")
        if geno_recs:
            n = b.table("CPIC recommendations for the patient's genotypes.",
                        ["Drug", "Phenotype", "Population", "Recommendation", "Strength", "Ref"],
                        [[_name(r["drug"]), r["phenotype"], _pop(r), r.get("recommendation"),
                          r.get("classification"), b.refs.cell([r["id"]])] for r in geno_recs],
                        [1.4, 1.8, 1.2, 3.6, 1.0, 0.8])
            b.p(f"Recommendations matched exactly to the patient's CPIC phenotype are given in Table {n}.")
    if guidelines:
        n = b.table("CPIC prescribing guidelines.", ["Gene–drug pair", "CPIC level", "PharmGKB level", "Guideline", "Ref"],
                    [[f"{g.get('gene')}–{g.get('drug')}", g.get("cpic_level"), g.get("clinpgx_level"),
                      g.get("guideline_name"), b.refs.cell([g["id"]])] for g in guidelines], [2.4, 1.2, 1.4, 3.6, 0.9])
        b.p(f"CPIC guidelines are summarised in Table {n}.")
    if annotations:
        best = sorted(annotations, key=lambda a: (PGKB_ORDER.index(a["level"]) if a.get("level") in PGKB_ORDER else 9,
                                                  -(a.get("score") or 0)))[:8]
        n = b.table("Highest-level PharmGKB clinical annotations (up to eight).",
                    ["Variant(s)", "Drug(s)", "Level", "Finding", "Ref"],
                    [[a.get("variant"), ", ".join(a.get("drugs") or []), a.get("level"),
                      (a.get("summary") or "")[:230] + ("…" if len(a.get("summary") or "") > 230 else ""),
                      b.refs.cell([a["id"]])] for a in best], [2.2, 1.5, 0.8, 4.4, 0.8])
        b.p(f"Variant-level clinical annotations are given in Table {n}.")
    if not guidelines and not annotations:
        b.p("No CPIC guideline or PharmGKB clinical annotation was found.")

    b.h2("3.5 Drug safety: interactions and warnings")
    if safety:
        checked = (name + " was" if is_drug
                   else "The {} best-supported drugs were".format(safety.get("checked_drugs", 0)))
        b.p(f"{checked} checked against the current medications ({_and(meds)})." if meds
            else "No current medications were given, so no drug–drug interaction check was run.")
        if alerts:
            b.table("Drug–drug interactions found by the checks, most severe first.",
                    ["Severity", "Drugs", "Finding", "Source", "Ref"],
                    [[cell(a["severity"].capitalize(), bold=True,
                           fill=RESIST_FILL if a["severity"] == "major" else "F4B183" if a["severity"] == "moderate" else None,
                           color="FFFFFF" if a["severity"] == "major" else None),
                      f"{a['drug']} with {a['other']}",
                      a["summary"] + (f" Label: “{a['label_text']}”" if a.get("label_text") else ""),
                      {"label class": "FDA label + FDA table", "label names drug": "FDA label", "FDA table": "FDA table",
                       "phenoconversion": "FDA table + CPIC"}.get(a["rule"], a["rule"]),
                      b.refs.cell([a.get("evidence_id")])] for a in alerts[:30]], [1.0, 2.0, 4.4, 1.3, 0.7],
                    note="Major: the label says to avoid the combination, a strong inhibitor or inducer meets a "
                         "sensitive substrate, or a strong inhibitor changes a pharmacogene's phenotype. "
                         "Pharmacokinetic interactions only.")
        elif meds:
            b.p("No pharmacokinetic interaction was found by the checks.")
        if warned:
            b.table("Drug warnings recorded in ChEMBL (withdrawals and boxed warnings).", ["Drug", "Warning"],
                    [[cell(drug, bold=True), "; ".join(w)] for drug, w in warned[:30]], [2.4, 7.0])
        elif not alerts:
            b.p("No ChEMBL drug warning was found for the drugs listed.")
    else:
        b.p("This search was run before the drug safety checks were added.")

    if pbs:
        b.h2("3.6 Availability in Australia")
        n = b.table("Listing of the interacting drugs on the Pharmaceutical Benefits Scheme (PBS).",
                    ["Drug", "PBS listed", "Benefit type", "Indication restrictions"],
                    [[cell(_name(a["drug"]), bold=True), "Yes" if a.get("pbs_listed") else "No",
                      ", ".join(sorted({i.get("benefit_type") for i in a.get("items") or [] if i.get("benefit_type")})),
                      _restrictions(a) if a.get("pbs_listed") else None] for a in pbs], [2.3, 1.3, 2.3, 3.5],
                    note="Matched by generic name on the current PBS Schedule; whether a patient meets a restriction "
                         "was not assessed.")
        b.p(f"{sum(1 for a in pbs if a.get('pbs_listed'))} of {len(pbs)} drugs checked are PBS-listed (Table {n}).")

    # ----- 4 Interpretation -----
    b.h1("4\tInterpretation")
    if not b.llm("primary", "Written by {model} from the evidence in Section 3 and reproduced without editing, apart "
                            "from citation numbers. Every factual sentence cites an evidence record."):
        b.p("No language-model interpretation is available for this query.")

    # ----- 5 Limitations -----
    b.h1("5\tLimitations and data gaps")
    b.bullets(["Live services are queried at the time of the search; results may differ if they are repeated later.",
               "Many DGIdb interactions have no curated interaction type and are reported as unspecified.",
               "Grades describe the strength of the published evidence, not clinical benefit in an individual patient.",
               "Drug–drug interaction checks cover pharmacokinetic interactions found in FDA labels and the FDA CYP / "
               "transporter table; additive toxicity, QT effects and food interactions are not checked.",
               "The narrative in Section 4 is machine-generated. Its citations were validated automatically, but its "
               "statements were not reviewed by an expert.",
               "PBS listing is matched by drug name; restrictions are summarised and were not assessed for any patient."])

    # ----- 6 Conclusion -----
    b.h1("6\tConclusion")
    b.p(conclusion + (f" {len(alerts)} drug–drug interaction alert(s) with the current medications need review."
                      if alerts else "") + " Every pair can be traced to its evidence records and grade, and page 1 "
        "summarises the strongest findings, prescribing guidance and safety flags.")
    b.references(DGI_BIB)
    b.provenance("Appendix A: Data provenance", [
        ["Input", q["entities"][0]],
        ["Relationship types", "all relationship types" if all_types else _and(labels(rel))],
        ["Current medications", ", ".join(meds) or "none given"],
        *([["Follow-up question", q["followup_question"]]] if q.get("followup_question") else []),
    ])
    return (f"{name}: drug–gene interaction report", name)


def _dgi_warned(q, rows, ev):
    out, seen = [], set()
    drug_warnings = (ev.get("safety") or {}).get("drug_warnings") or []
    if drug_warnings:
        out.append((_name(q["entities"][0]), drug_warnings))
    for r in rows:
        if r.get("warnings") and r["drug"] not in seen:
            seen.add(r["drug"])
            out.append((_name(r["drug"]), r["warnings"]))
    return out


# ---------- Packaging ----------

def build_report(query, figure=None, figure_note=None):
    """query: Query.to_dict(). figure: PNG bytes for the network figure, or None. Returns (docx bytes, file name)."""
    if figure is not None:
        if len(figure) > MAX_FIGURE_BYTES:
            raise ValueError("the figure is too large")
        _png_size(figure)
    b = Builder(query, figure, figure_note)
    header, slug = (build_vcf if query.get("entity_type") == "vcf" else build_dgi)(b)
    title = re.sub(r"<[^>]+>", "", next((x for x in b.body if 'w:val="Title"' in x), ""))
    title = re.sub(r"\s+", " ", title).strip()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    out = BytesIO()
    with zipfile.ZipFile(BASE_DOCX) as base, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as docx:
        for info in base.infolist():
            data = base.read(info.filename).decode("utf-8")
            if info.filename == "word/document.xml":
                data = data.replace("{{BODY}}", "".join(b.body))
            elif info.filename == "word/header1.xml":
                data = data.replace("{{HEADER_TITLE}}", escape(header)).replace("{{HEADER_RIGHT}}", f"Query #{query['id']}")
            elif info.filename == "docProps/core.xml":
                data = (data.replace("{{TITLE}}", escape(title)).replace("{{NOW}}", now)
                        .replace("{{DESCRIPTION}}", f"Pharmagenix report {b.number}"))
            elif info.filename == "word/_rels/document.xml.rels" and figure:
                data = data.replace("</Relationships>", '<Relationship Id="rIdFigure1" Type="http://schemas.openxmlformats.'
                                    'org/officeDocument/2006/relationships/image" Target="media/figure1.png"/></Relationships>')
            docx.writestr(info, data)
        if figure:
            docx.writestr("word/media/figure1.png", figure)
    filename = f"Pharmagenix_report_{b.number}_{re.sub(r'[^A-Za-z0-9_.-]+', '_', slug).strip('_')}.docx"
    return out.getvalue(), filename
