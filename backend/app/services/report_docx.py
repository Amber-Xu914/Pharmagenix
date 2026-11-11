"""Downloadable Word report (.docx) for a query, in the Pharmagenix report layout (docs/report_templates).

Everything except Section 4 comes from the stored evidence bundle by fixed rules (the same tables, grades, tiers and
scores the results page shows); Section 4 is the LLM text exactly as stored, with its evidence IDs ([E3]) renumbered
to the reference list. Nothing is generated or summarised here, so the document can be rebuilt at any time and always
matches the page.

The package is assembled from report_assets/report_base.docx (styles, page setup, header and footer of the layout)
with a new body, header text and an optional network figure, using plain OOXML: no extra dependency.
"""

import re
import struct
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from .relations import DEFAULT_RELATION_TYPES, labels

BASE_DOCX = Path(__file__).resolve().parent.parent / "report_assets" / "report_base.docx"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
CONTENT_WIDTH = 9412          # text width of the base layout, in twentieths of a point
FIGURE_MAX_EMU = 5_760_000    # 16 cm
MAX_FIGURE_BYTES = 8_000_000
NOTICE = ("Research and teaching use only. This report summarises database and literature evidence and machine-generated "
          "interpretation; it is not a clinical report and makes no treatment recommendation.")
ENDMATTER = ("Tables, grades, tiers and scores were produced by deterministic rules from the cited sources; only Section 4 "
             "is machine-written.")
AI_ROLES = {"primary": "Analysis", "literature": "Literature", "biology": "Biological interpretation",
            "hypothesis": "Hypotheses"}
GRADE_ORDER = ["strong", "moderate", "supportive", "limited"]
PGKB_ORDER = ["1A", "1B", "2A", "2B", "3", "4"]
RESISTANT = {"Resistance", "Reduced Sensitivity"}
DROP_STEPS = {"qc": "Quality filter", "germline": "Germline", "consequence": "Consequence"}

GENE_SOURCES = [
    ("dgidb", "DGIdb", "Drug–gene interactions and types"),
    ("chembl", "ChEMBL", "Curated mechanisms of action"),
    ("cpic", "CPIC", "Genotype-based prescribing guidelines"),
    ("pharmgkb", "PharmGKB / ClinPGx", "Clinical annotations of variants"),
    ("pubtator", "PubTator3", "Literature for the strongest pairs"),
    ("uniprot", "UniProt", "Protein function"),
    ("drugbank", "DrugBank", "Drug descriptions"),
    ("pbs", "PBS Schedule", "Australian subsidy listing"),
]
VCF_SOURCES = [
    ("vep", "Ensembl VEP", "Consequence, HGVS, population frequency, pathogenicity scores"),
    ("civic", "CIViC", "Variant-level clinical evidence"),
    ("oncokb", "OncoKB", "Variant-level therapeutic levels"),
    ("dgidb", "DGIdb", "Gene-level drug interactions"),
    ("chembl", "ChEMBL", "Drug mechanisms of action (gene level)"),
    ("pubtator", "PubTator3", "Literature for the exploratory track"),
    ("medcpt", "MedCPT", "Re-ranking of the retrieved literature"),
    ("pbs", "PBS Schedule", "Australian subsidy listing"),
]
SOURCE_NAMES = {key: name for key, name, _ in GENE_SOURCES + VCF_SOURCES}


# ---------- OOXML building blocks ----------

def _t(text):
    return f'<w:t xml:space="preserve">{escape(str(text))}</w:t>'


def run(text, bold=False, italic=False, style=None, rpr=""):
    props = (f'<w:rStyle w:val="{style}"/>' if style else "") + ("<w:b/><w:bCs/>" if bold else "") + \
            ("<w:i/><w:iCs/>" if italic else "") + rpr
    inner = "<w:tab/>".join(_t(part) if part else "" for part in str(text).split("\t"))
    return f"<w:r>{f'<w:rPr>{props}</w:rPr>' if props else ''}{inner}</w:r>"


def ptab(text):
    """Right-aligned text on the same line (series line, header)."""
    return f'<w:r><w:ptab w:alignment="right" w:relativeTo="margin" w:leader="none"/>{_t(text)}</w:r>'


def para(style, *runs, ppr=""):
    body = "".join(r if r.startswith("<") else run(r) for r in runs if r)
    return f'<w:p><w:pPr><w:pStyle w:val="{style}"/>{ppr}</w:pPr>{body}</w:p>'


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
_LINE = '<w:{0} w:val="single" w:color="111111" w:sz="{1}"/>'
_CELL_MARGIN = ('<w:tcMar><w:top w:type="dxa" w:w="40"/><w:left w:type="dxa" w:w="80"/>'
                '<w:bottom w:type="dxa" w:w="40"/><w:right w:type="dxa" w:w="80"/></w:tcMar>')


def _cell(width, content, style, top=None, bottom=None, header=False):
    borders = "".join([
        _LINE.format("top", top) if top else _NONE.format("top"), _NONE.format("left"),
        _LINE.format("bottom", bottom) if bottom else _NONE.format("bottom"), _NONE.format("right"),
    ])
    runs = content if isinstance(content, list) else [run("–" if content in (None, "") else content)]
    valign = '<w:vAlign w:val="bottom"/>' if header else ""
    return (f'<w:tc><w:tcPr><w:tcW w:type="dxa" w:w="{width}"/><w:tcBorders>{borders}</w:tcBorders>{_CELL_MARGIN}'
            f'{valign}</w:tcPr>'
            f'<w:p><w:pPr><w:pStyle w:val="{style}"/><w:jc w:val="left"/></w:pPr>{"".join(runs)}</w:p></w:tc>')


def table(headers, rows, weights):
    """Three-rule table as in the template: rule above and below the header, rule under the last row."""
    total = sum(weights)
    widths = [int(CONTENT_WIDTH * w / total) for w in weights]
    widths[-1] += CONTENT_WIDTH - sum(widths)
    no_borders = "".join(_NONE.format(side) for side in ("top", "left", "bottom", "right", "insideH", "insideV"))
    grid = "".join(f'<w:gridCol w:w="{w}"/>' for w in widths)
    xml = [f'<w:tbl><w:tblPr><w:tblW w:type="dxa" w:w="{CONTENT_WIDTH}"/><w:tblBorders>{no_borders}</w:tblBorders>'
           f'</w:tblPr><w:tblGrid>{grid}</w:tblGrid>',
           '<w:tr><w:trPr><w:cantSplit/><w:tblHeader/></w:trPr>'
           + "".join(_cell(w, h, "TableHeading", top=8, bottom=4, header=True) for w, h in zip(widths, headers))
           + "</w:tr>"]
    for i, row in enumerate(rows):
        last = i == len(rows) - 1
        xml.append("<w:tr><w:trPr><w:cantSplit/></w:trPr>"
                   + "".join(_cell(w, c, "TableText", bottom=8 if last else None) for w, c in zip(widths, row))
                   + "</w:tr>")
    return "".join(xml) + "</w:tbl>"


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
                    r"|\[(?P<lt>[^\]]+)\]\((?P<lu>[^)]+)\)|" + CITATION.pattern)


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
    out, pos = [], 0
    for m in INLINE.finditer(text):
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
        else:
            cited = refs.cite(_expand(m.group(m.lastindex)))
            out += cited or [run(m.group(0), bold, italic)]  # an ID not in the evidence stays visible as written
        pos = m.end()
    if pos < len(text):
        out.append(run(text[pos:], bold, italic))
    return out


def markdown_paragraphs(md, refs):
    """The LLM's Markdown as AI-styled paragraphs: ## / ### headings, one paragraph per block or list item."""
    paras, buffer = [], []

    def flush():
        if buffer:
            paras.append(para("AIText", *inline_runs(" ".join(buffer), refs)))
            buffer.clear()

    for raw in (md or "").splitlines():
        line = raw.strip()
        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        item = re.match(r"^(?:[-*+]|\d+[.)])\s+(.*)$", line)
        if not line or re.match(r"^(-{3,}|\*{3,}|\|?\s*:?-{3,}.*)$", line):
            flush()
        elif heading:
            flush()
            if len(heading.group(1)) > 1:  # the "# Title" line repeats the report title
                paras.append(para("AIHeading", *inline_runs(heading.group(2).strip("# "), refs)))
        elif item:
            flush()
            buffer.append(item.group(1))
            flush()
        elif line.startswith("|"):
            flush()
            cells = [c.strip() for c in line.strip("|").split("|")]
            buffer.append(" · ".join(c for c in cells if c))
            flush()
        else:
            buffer.append(line)
    flush()
    return paras


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
    if k == "oncokb":
        drugs = ", ".join(g("drugs") or g("therapies") or [])
        return f"OncoKB: {g('gene') or ''} {g('alteration') or ''} and {drugs}; level {g('level')}. OncoKB.".replace("  ", " ")
    return f"{g('source') or 'Evidence'} record {g('id')}."


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


# ---------- The document ----------

class Builder:
    def __init__(self, query, figure=None, figure_note=None):
        self.q = query
        self.ev = query.get("evidence") or {}
        self.refs = Refs(self.ev.get("items") or [])
        self.body = []
        self.tables = 0
        self.figure = figure
        self.figure_note = figure_note or {}
        self.number = f"PGX-{query['id']:05d}"
        self.reports = {}
        for r in sorted(query.get("reports") or [], key=lambda r: r["id"]):
            if r.get("status") in ("success", "flagged") and r.get("content_md"):
                self.reports[r["role"]] = r  # the latest of each role

    # helpers
    def add(self, *xml):
        self.body.extend(xml)

    def h1(self, text):
        self.add(para("Heading1", text, ppr='<w:tabs><w:tab w:val="left" w:pos="440"/></w:tabs>'))

    def h2(self, text):
        self.add(para("Heading2", text))

    def p(self, *runs, style="Normal"):
        self.add(para(style, *runs))

    def table(self, caption, headers, rows, weights, note=None):
        self.tables += 1
        self.add(para("Caption", run(f"Table {self.tables}. ", bold=True), caption), table(headers, rows, weights))
        if note:
            self.add(para("TableNote", note))
        self.add(para("Normal"))  # space before the next paragraph, as in the template
        return self.tables

    def front(self, series, title, byline, meta, abstract, keywords):
        self.add(para("SeriesLine", series, ptab(f"Report {self.number}")), para("Title", title),
                 para("Byline", byline), para("BylineMeta", meta), para("AbstractHeading", "Abstract"))
        for label, text in abstract:
            self.add(para("AbstractText", run(f"{label}. ", bold=True), text))
        self.add(para("Keywords", run("Keywords: ", bold=True), "; ".join(keywords)), para("Notice", NOTICE))

    def sources_table(self, catalogue):
        status = self.ev.get("source_status") or {}
        rows = []
        for key, name, content in catalogue:
            s = status.get(key)
            if not s:
                continue
            state = s.get("state")
            version = s.get("version") or {"ok": "live API, accessed at query time", "disabled": "not configured",
                                           "error": "failed at query time", "skipped": "not run"}.get(state, state)
            records = s.get("count") if state == "ok" else {"disabled": "not used", "skipped": "not run"}.get(state, "failed")
            rows.append([name, content, version, records])
        return self.table("Data sources, versions and records retrieved for this query.",
                          ["Source", "Content used", "Version / access", "Records"], rows, [2.2, 3.2, 3.2, 1.1],
                          note="Records: number retrieved for this query (after relationship-type filtering).")

    def figure_block(self, caption):
        if not self.figure:
            return False
        self.add(figure_xml("rIdFigure1", self.figure, "Figure 1", caption),
                 para("FigureCaption", run("Figure 1. ", bold=True), caption))
        return True

    def discussion(self, roles, note):
        present = [r for r in roles if r in self.reports]
        if not present:
            self.p("No language-model interpretation is available for this query.")
            return
        models = sorted({self.reports[r]["model"] for r in present})
        self.add(para("AINote", note.format(models=_and(models), n=len(present))))
        for i, role in enumerate(present, 1):
            if len(present) > 1:
                self.add(para("AIHeading", f"4.{i} {AI_ROLES.get(role, role.title())}"))
            self.add(*markdown_paragraphs(self.reports[role]["content_md"], self.refs))

    def references(self):
        self.add(para("Heading1", "References"))
        if not self.refs.order:
            self.p("No evidence item is cited.")
        for n, eid in enumerate(self.refs.order, 1):
            item = self.refs.items[eid]
            url = item.get("url")
            self.add(
                f'<w:p><w:pPr><w:pStyle w:val="Reference"/><w:tabs><w:tab w:val="left" w:pos="520"/></w:tabs></w:pPr>'
                f'<w:bookmarkStart w:name="ref_{n}" w:id="{n}"/>{run(f"[{n}]")}<w:bookmarkEnd w:id="{n}"/>'
                f'{run(chr(9))}{run(reference_text(item))}'
                + (run(f" {url}", style="Code", rpr='<w:color w:val="1A3D7C"/><w:sz w:val="14"/><w:szCs w:val="14"/>')
                   if url else "") + "</w:p>")

    def provenance(self, heading, extra_rows):
        self.h1(heading)
        rows = [["Query", f"#{self.q['id']}, {_date(self.q.get('created_at'), time=True)}"],
                ["Requested by", "signed-in user" if self.q.get("user_id") else "anonymous user"], *extra_rows]
        for role, r in sorted(self.reports.items(), key=lambda kv: list(AI_ROLES).index(kv[0]) if kv[0] in AI_ROLES else 9):
            template = f", prompt template v{r['template_version']}" if r.get("template_version") else ""
            latency = f"; {r['latency_ms']} ms" if r.get("latency_ms") else ""
            rows.append([f"Language model ({role})",
                         f"{r['model']}{template}; {r['status']}; {len(r.get('citations') or [])} citations, "
                         f"{len(r.get('invalid_citations') or [])} invalid{latency}"])
        self.table("Query and processing record.", ["Item", "Value"], rows, [2.5, 7])
        self.add(para("Endmatter", f"Pharmagenix report {self.number}. {ENDMATTER}"))


# ---------- Gene → drugs / drug → genes ----------

def build_dgi(b):
    q, ev = b.q, b.ev
    is_drug = q.get("entity_type") == "drug"
    norm = (ev.get("normalisation") or [{}])[0]
    profile = next((i for i in ev.get("items") or [] if i.get("kind") == "profile"), {})
    name = norm.get("name") or q["entities"][0]
    name = _name(name) if is_drug else name  # gene symbols stay as HGNC writes them
    full = profile.get("full_name") if not is_drug else None
    subject = f"{name} ({full})" if full and full.upper() != name.upper() else name
    table_rows = ev.get("table") or []
    grades = {g: sum(1 for r in table_rows if r.get("grade") == g) for g in GRADE_ORDER}
    items = ev.get("items") or []
    guidelines = [i for i in items if i.get("kind") == "guideline"]
    annotations = [i for i in items if i.get("kind") == "variant_annotation"]
    pbs = ev.get("australian_availability") or []
    status = ev.get("source_status") or {}
    used = [name for key, name, _ in GENE_SOURCES if status.get(key, {}).get("state") == "ok"]
    primary = b.reports.get("primary")
    rel = q.get("relation_types") or []
    rel_text = "all relationship types" if set(rel) >= set(DEFAULT_RELATION_TYPES) else \
        "these relationship types: " + _and([x.lower() for x in labels(rel)])
    partner = "genes" if is_drug else "drugs"
    cpic_ab = sorted({(i.get("gene") if is_drug else i.get("drug") or "").strip() for i in guidelines
                      if str(i.get("cpic_level") or "").startswith(("A", "B"))} - {""})
    top_annotations = [a for a in annotations if a.get("level") in ("1A", "1B")]

    results = [f"{len(table_rows)} drug–gene pairs were identified: {grades['strong']} strong, {grades['moderate']} "
               f"moderate, {grades['supportive']} supportive and {grades['limited']} limited."]
    if cpic_ab:
        results.append(f"CPIC level A/B prescribing guidelines exist for {_and(cpic_ab)}.")
    if annotations:
        results.append(f"{len(annotations)} PharmGKB clinical annotations were retrieved ({len(top_annotations)} at "
                       f"level 1A/1B).")
    if pbs:
        results.append(f"{sum(1 for a in pbs if a.get('pbs_listed'))} of {len(pbs)} drugs checked are listed on the "
                       f"Australian PBS.")
    strong = [r["gene"] if is_drug else _name(r["drug"]) for r in table_rows if r.get("grade") == "strong"]
    if cpic_ab:
        conclusion = (f"Genotype-guided prescribing is supported by guideline-level evidence for {_and(cpic_ab)}; the "
                      f"remaining interactions rest on lower-level or database-only evidence.")
    elif strong:
        conclusion = f"Strong evidence supports the interaction with {_and(strong[:5])}; the rest is weaker."
    elif table_rows:
        conclusion = "No pair reached the strong grade; the interactions found rest on moderate or lower evidence."
    else:
        conclusion = f"No drug–gene interaction was found for {name} in the sources searched."

    b.front(
        "Pharmagenix · Drug–gene interaction report",
        f"{'Gene' if is_drug else 'Drug'} interactions of {subject}: an evidence summary",
        "Pharmagenix automated evidence report",
        f"Query #{q['id']} · searched {_date(q.get('created_at'))} · report generated "
        f"{_date(datetime.now(timezone.utc))}",
        [("Objective", f"To identify the {partner} that interact with {subject} and to summarise the supporting "
                       f"evidence, considering {rel_text}."),
         ("Methods", f"Evidence was retrieved from {len(used)} sources ({_and(used)}), merged per drug–gene pair and "
                     f"graded with fixed rules."
                     + (f" A narrative interpretation was generated by a large language model ({primary['model']}"
                        f"{', prompt template v' + str(primary['template_version']) if primary.get('template_version') else ''}"
                        f") constrained to cite the retrieved evidence." if primary else "")),
         ("Results", " ".join(results)),
         ("Conclusion", conclusion)],
        [name, "pharmacogenomics", "drug–gene interaction"] + (["CPIC"] if guidelines else []) + ["evidence grading"],
    )

    b.h1("1\tIntroduction")
    b.p(f"This report was generated by Pharmagenix, a research prototype that integrates curated pharmacogenomic "
        f"databases with live literature and interaction services, in response to a query for the {partner} that "
        f"interact with the {'drug' if is_drug else 'gene'} {name}. Its purpose is to collect the published evidence "
        f"in one place and to make its strength explicit; it does not make treatment recommendations.")

    b.h1("2\tMethods")
    b.h2("2.1 Entity resolution")
    if norm:
        b.p(f"The input “{norm.get('input') or q['entities'][0]}” was resolved to {name} ({norm.get('id')}) by "
            f"{norm.get('matched_by') or 'exact match'}.")
    else:
        b.p(f"The input “{q['entities'][0]}” was used as entered.")
    b.h2("2.2 Data sources")
    n = b.sources_table(GENE_SOURCES)
    b.p(f"Sources, versions and the number of records retrieved are given in Table {n}. Local datasets were imported "
        f"in full and versioned; the others were queried live at the time of the search.")
    b.h2("2.3 Evidence integration and grading")
    n = b.table("Evidence grades, assigned by fixed rules to each drug–gene pair (the strongest evidence decides).",
                ["Grade", "Criteria"], [
                    ["Strong", "CPIC level A or B guideline, or PharmGKB clinical annotation level 1A/1B"],
                    ["Moderate", "PharmGKB level 2A/2B; curated ChEMBL mechanism of action; or ≥3 DGIdb sources with "
                                 "literature"],
                    ["Supportive", "Lower-level pharmacogenomic evidence (CPIC C/D, PharmGKB 3), or ≥2 DGIdb sources or "
                                   "literature"],
                    ["Limited", "A single database record without literature"]], [1.6, 7.8])
    b.p(f"Records describing the same pair were merged and graded as in Table {n}. Opposite directions of action for "
        f"the same pair are flagged as conflicts. Grades are not assigned or altered by the language model.")
    b.h2("2.4 Narrative generation")
    if primary:
        b.p(f"The interpretation in Section 4 was generated by {primary['model']}"
            f"{', prompt template v' + str(primary['template_version']) if primary.get('template_version') else ''}. "
            f"The model received the graded pairs and the evidence records only, was instructed to cite an evidence "
            f"identifier for every factual sentence, and its output was checked automatically: "
            f"{len(primary.get('citations') or [])} citations, {len(primary.get('invalid_citations') or [])} to "
            f"evidence not supplied. Citations are renumbered here to match the reference list.")
    else:
        b.p("No language-model interpretation was produced for this query.")

    b.h1("3\tResults")
    b.h2("3.1 Overview")
    b.p(f"In total {len(table_rows)} pairs were identified{' (Figure 1)' if b.figure else ''}: {grades['strong']} "
        f"strong, {grades['moderate']} moderate, {grades['supportive']} supportive and {grades['limited']} limited.")
    shown, total = b.figure_note.get("shown"), b.figure_note.get("total")
    b.figure_block(f"Interaction network of {name}. Each line joins the queried {'drug' if is_drug else 'gene'} to an "
                   f"interacting {'gene' if is_drug else 'drug'}; line style and arrowhead show the relationship type."
                   + (f" Showing {shown} of {total} relationships." if shown and total and shown < total else ""))

    b.h2("3.2 Drug–gene interactions")
    supported = [r for r in table_rows if r.get("grade") != "limited"][:40]
    omitted = len(table_rows) - len(supported)
    if supported:
        n = b.table("Drug–gene pairs graded strong, moderate or supportive.",
                    ["Gene" if is_drug else "Drug", "Relationship", "Grade", "Highest level", "Sources", "Refs"],
                    [[r["gene"] if is_drug else _name(r["drug"]), r.get("relation") or "unspecified",
                      r.get("grade_label") or r.get("grade"), r.get("level") or "–", ", ".join(r.get("sources") or []),
                      b.refs.cell(r.get("evidence_ids"))] for r in supported],
                    [2.0, 2.4, 1.2, 1.9, 1.9, 1.0],
                    note=f"{omitted} further pairs (graded limited, or beyond the first 40) are omitted." if omitted else None)
        b.p(f"The supported pairs are listed in Table {n}.")
    else:
        b.p("No pair was graded above limited.")

    b.h2("3.3 Pharmacogenomic evidence")
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

    if pbs:
        b.h2("3.4 Availability in Australia")
        n = b.table("Listing of the interacting drugs on the Pharmaceutical Benefits Scheme (PBS).",
                    ["Drug", "PBS listed", "Benefit type", "Indication restrictions"],
                    [[_name(a["drug"]), "Yes" if a.get("pbs_listed") else "No",
                      ", ".join(sorted({i.get("benefit_type") for i in a.get("items") or [] if i.get("benefit_type")})),
                      _restrictions(a) if a.get("pbs_listed") else None] for a in pbs], [2.3, 1.3, 2.3, 3.5],
                    note="Matched by generic name on the current PBS Schedule; whether a patient meets a restriction "
                         "was not assessed.")
        b.p(f"{sum(1 for a in pbs if a.get('pbs_listed'))} of {len(pbs)} drugs checked are PBS-listed (Table {n}).")

    b.h1("4\tDiscussion")
    b.discussion(["primary"], "The following interpretation was written by {models} from the evidence in Section 3 and "
                              "is reproduced without editing, apart from citation numbers.")

    b.h1("5\tLimitations")
    for line in ["Live services are queried at the time of the search; results may differ if they are repeated later.",
                 "Many DGIdb interactions have no curated interaction type and are reported as unspecified.",
                 "Grades describe the strength of the published evidence, not clinical benefit in an individual patient.",
                 "The narrative in Section 4 is machine-generated. Its citations were validated automatically, but its "
                 "statements were not reviewed by an expert.",
                 "PBS listing is matched by drug name; restrictions are summarised and were not assessed for any patient."]:
        b.p(line)
    b.references()
    b.provenance("Appendix A: Data provenance", [
        ["Input", q["entities"][0]],
        ["Relationship types", "all relationship types" if set(rel) >= set(DEFAULT_RELATION_TYPES) else _and(labels(rel))],
        *([["Follow-up question", q["followup_question"]]] if q.get("followup_question") else []),
    ])
    return (f"{name}: drug–gene interaction report", name)


PRESCRIBER_RULES = ("For prescribing by", "The condition must be stable for the prescriber")


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

def build_vcf(b):
    q, ev = b.q, b.ev
    inp = ev.get("input") or {}
    cancer = inp.get("cancer_type") or {}
    variants = ev.get("variants") or {}
    counts = variants.get("counts") or {}
    tiered = variants.get("tiered") or []
    tracks = ev.get("tracks") or {}
    established = tracks.get("established") or []
    exploratory = tracks.get("exploratory") or []
    resist_only = ev.get("resistance_only") or []
    params = ev.get("parameters") or {}
    qc, t3 = params.get("qc") or {}, params.get("tier3") or {}
    items = b.refs.items
    variant_item = {i.get("variant_id"): i["id"] for i in items.values() if i.get("kind") == "variant"}
    sample = inp.get("example") or inp.get("filename") or q["entities"][0]
    cancer_name = cancer.get("name") or "unknown cancer type"
    code = cancer.get("code") or "?"
    status = ev.get("source_status") or {}
    used = [SOURCE_NAMES.get(k, k) for k in ("civic", "oncokb", "dgidb", "chembl") if status.get(k, {}).get("state") == "ok"]
    models = sorted({r["model"] for r in b.reports.values()})
    top = next((c for c in established if not c.get("strong_resistance")), None)
    down = {}
    for c in established + exploratory:
        if c.get("strong_resistance") and c.get("strong_resistance_text"):
            down.setdefault(c["strong_resistance_text"], []).append(c)

    results = [f"Of {counts.get('total', 0)} variants, {counts.get('qc_passed', 0)} were somatic and protein-altering: "
               f"{counts.get('tier1', 0)} Tier 1, {counts.get('tier2', 0)} Tier 2 and {counts.get('tier3', 0)} Tier 3."]
    if top:
        results.append(f"The highest-ranked established option was {_lc(top['drug'])} "
                       f"(score {top['score']}; {top.get('track_reason')}).")
    for text, cs in down.items():
        results.append(f"{_and(sorted(_lc(c['drug']) for c in cs))} {'were' if len(cs) > 1 else 'was'} down-ranked because "
                       f"of {text}.")
    if resist_only:
        results.append(f"{len(resist_only)} drug{'s' if len(resist_only) != 1 else ''} had resistance evidence only.")
    conclusion = (f"Established evidence supports {_lc(top['drug'])} for "
                  f"this variant profile; exploratory candidates are hypotheses for further study." if top else
                  (ev.get("outcome") or {}).get("no_targeted_therapy")
                  or "No established targeted therapy was found; exploratory candidates are hypotheses for further study.")
    genes = sorted({v.get("gene") for v in tiered if v.get("gene")})

    b.front(
        "Pharmagenix · Precision oncology research report",
        f"Candidate therapies for a {cancer_name.lower()} variant profile ({sample})",
        "Pharmagenix automated evidence report · synthetic test data",
        f"Query #{q['id']} · analysed {_date(q.get('created_at'))} · report generated "
        f"{_date(datetime.now(timezone.utc))}",
        [("Objective", f"To identify candidate drugs for the somatic variants of a {cancer_name.lower()} "
                       f"(OncoTree {code}) sample, separating established clinical evidence from exploratory predictions."),
         ("Methods", "Variants were filtered for quality and germline origin, annotated with Ensembl VEP, tiered by fixed "
                     "rules against CIViC, cancerhotspots.org, IntOGen and cBioPortal, and linked to drugs through "
                     f"{_and(used) or 'the drug evidence sources'}. Drugs were ranked separately on an established and an "
                     "exploratory track."
                     + (f" Language-model agents ({_and(models)}) summarised the results under a citation constraint."
                        if models else "")),
         ("Results", " ".join(results)),
         ("Conclusion", conclusion)],
        [code, "somatic variants", *genes, "CIViC", "variant tiering"],
    )

    b.h1("1\tIntroduction")
    b.p(f"This report summarises an automated analysis of the somatic variant file {inp.get('filename') or sample} from a "
        f"{cancer_name.lower()} sample. The file is synthetic test data, used to evaluate the "
        f"Pharmagenix research prototype; the report does not concern a real patient and does not make treatment "
        f"recommendations.")

    b.h1("2\tMethods")
    b.h2("2.1 Variant quality control and annotation")
    b.p(f"Variants were retained when FILTER was PASS, read depth ≥ {qc.get('min_depth')}, alternate-allele reads ≥ "
        f"{qc.get('min_alt_reads')} and variant allele fraction ≥ {_pct(qc.get('min_vaf'))}. Variants were considered "
        f"germline and removed when their gnomAD allele frequency exceeded {_pct(qc.get('max_population_af'))} or the "
        f"matched normal sample had more than {qc.get('max_normal_alt_reads')} alternate read"
        f"{'s' if qc.get('max_normal_alt_reads') != 1 else ''}. Remaining variants were annotated with Ensembl VEP "
        f"({inp.get('assembly') or 'GRCh38'}), and only protein-altering consequences were kept.")
    b.h2("2.2 Variant tiering")
    w = t3.get("weights") or {}
    bp = t3.get("b_parts") or {}
    n = b.table("Tiering rules (the first rule that applies decides).", ["Tier", "Criterion"], [
        ["1", "CIViC level A/B (or OncoKB level 1, 2 or R1) evidence in this cancer type or a parent type, matched to the "
              "exact variant, codon or exon (or, for a truncating variant in a tumour suppressor, the variant class)"],
        ["2", "A known hotspot (cancerhotspots.org), a truncating variant in a tumour-suppressor driver of this cancer "
              "type (IntOGen), or variant-level evidence in other cancer types"],
        ["3", f"Otherwise: S = {w.get('F')}·F + {w.get('R')}·R + {w.get('B')}·B, where F is the predicted functional "
              f"impact (AlphaMissense, REVEL or CADD), R the gene's mutation frequency in this subtype (cBioPortal; "
              f"{_pct(t3.get('r_saturation'))} = 1) and B the biological evidence (cancer gene {bp.get('cancer_role')}, "
              f"core pathway {bp.get('pathway')}, tumour-suppressor loss {bp.get('tsg_loss')}). Variants with S < "
              f"{t3.get('min_score')} are kept as low priority"]], [0.8, 8.6])
    b.p(f"Each retained variant was assigned a tier by the rules in Table {n}.")
    b.h2("2.3 Drug evidence and ranking")
    n = b.sources_table(VCF_SOURCES)
    b.p(f"Drug evidence was retrieved from the sources in Table {n} and merged per drug. A drug entered the established "
        "track when it had CIViC level A/B (or OncoKB level 1–2) evidence in this cancer type for a Tier 1 variant, "
        "matched at the variant, codon or exon level; all other links (lower levels, other cancer types, gene-level "
        "links, Tier 3 variants) formed the exploratory track. Each track was ranked separately by a points score; "
        "co-occurring variants were evaluated together, and resistance evidence from a variant present in the sample "
        "down-ranked the affected drugs.")
    b.h2("2.4 Language-model analysis")
    primary = b.reports.get("primary")
    if primary:
        agents = [AI_ROLES[r].lower() for r in ("literature", "biology", "hypothesis") if r in b.reports]
        b.p(f"An analysis agent ({primary['model']}"
            f"{', prompt template v' + str(primary['template_version']) if primary.get('template_version') else ''}) "
            f"summarised both tracks"
            + (f", and {len(agents)} exploration agent{'s' if len(agents) != 1 else ''} ({_and(agents)}) worked on the "
               f"exploratory track only" if agents else "")
            + ". The agents received the fixed tiers, scores and evidence records, could not change them, and had to "
              "cite an evidence identifier or label a statement as a hypothesis; outputs were checked automatically.")
    else:
        b.p("No language-model analysis was produced for this query.")

    b.h1("3\tResults")
    b.h2("3.1 Variants")
    b.figure_block("Variant–drug network: the tiered variants, their genes and the candidate drugs; line style shows "
                   "response, resistance or a gene-level link.")
    if tiered:
        n = b.table("Tiered variants.", ["Tier", "Variant", "cDNA change", "Consequence", "VAF", "Basis", "Ref"],
                    [[str(v.get("tier")), v.get("notation"), (v.get("hgvsc") or "").split(":")[-1] or None,
                      (v.get("consequence") or "").replace("_", " "), _pct(v.get("vaf")), v.get("tier_reason"),
                      b.refs.cell([variant_item.get(v.get("id"))])] for v in tiered],
                    [0.6, 1.6, 1.4, 1.5, 0.7, 3.2, 0.7])
        b.p(f"Of {counts.get('total', 0)} variants in the file, {counts.get('qc_passed', 0)} passed quality control and "
            f"{len(tiered)} were tiered{' (Figure 1; ' if b.figure else ' ('}Table {n}). Variants removed before tiering "
            f"are listed in Appendix A.")
    else:
        b.p("No variant passed quality control and tiering; the removed variants are listed in Appendix A.")

    pbs = {a["drug"].upper(): a for a in ev.get("australian_availability") or []}

    def pbs_cell(drug):
        a = pbs.get(drug.split(" + ")[0].upper())
        return None if a is None else ("Listed" if a.get("pbs_listed") else "Not listed")

    def best_cell(c):
        parts = []
        if c.get("best"):
            parts.append(f"CIViC {c['best']['level']} · {c['best'].get('disease_match')}")
        if c.get("oncokb_level"):
            parts.append(f"OncoKB {c['oncokb_level'].replace('LEVEL_', '')}")
        if not parts and c.get("gene_links"):
            parts.append(" / ".join(sorted({g["source"] for g in c["gene_links"]})) + " (gene level)")
        return "; ".join(parts) or None

    b.h2("3.2 Established clinical evidence")
    rows = [c for c in established if c.get("top")]
    if rows:
        n = b.table("Established track: drugs with level A/B evidence in this cancer type, in rank order.",
                    ["Rank", "Drug", "Target variant(s)", "Best evidence", "Score", "PBS", "Refs"],
                    [[str(c["rank"]), c["drug"], ", ".join(c.get("variants") or []), best_cell(c), str(c["score"]),
                      pbs_cell(c["drug"]), b.refs.cell(c.get("evidence_ids"))] for c in rows],
                    [0.6, 2.0, 1.8, 2.0, 0.7, 0.9, 1.4],
                    note="Score: points for evidence level, cancer-type match, variant specificity and supporting items, "
                         "minus points for resistance. PBS: listed on the Australian Pharmaceutical Benefits Scheme.")
        b.p(f"{len(established)} drug{'s' if len(established) != 1 else ''} reached the established track; the "
            f"top-ranked are listed in Table {n}.")
    else:
        b.p((ev.get("outcome") or {}).get("no_targeted_therapy")
            or "No drug has level A/B evidence for these variants in this cancer type.")

    b.h2("3.3 Resistance")
    sentences = []
    for text, cs in down.items():
        eids = [e for c in cs for e in c.get("evidence_ids") or []
                if items.get(e, {}).get("significance") in RESISTANT
                and set(items[e].get("variant_ids") or []) & set(c.get("resistance_variant_ids") or [])]
        sentences += [run(f"{text} conferred resistance to {_and(sorted(c['drug'].lower() for c in cs))}, which "
                          f"{'were' if len(cs) > 1 else 'was'} down-ranked "), *b.refs.cite(eids), run(". ")]
    for c in resist_only:
        sentences += [run(f"{c['drug']} had resistance evidence only ({'; '.join(c.get('resistance_notes') or [])}) "),
                      *b.refs.cite(c.get("evidence_ids")), run(". ")]
    b.p(*sentences) if sentences else b.p("No resistance evidence was found for the variants in this sample.")

    b.h2("3.4 Exploratory predictions")
    rows = [c for c in exploratory if c.get("top")]
    if rows:
        n = b.table("Exploratory track: the highest-ranked candidates. These are hypotheses, not evidence of response.",
                    ["Rank", "Drug", "Target variant(s)", "Basis", "Score", "Refs"],
                    [[str(c["rank"]), c["drug"], ", ".join(c.get("variants") or []), c.get("track_reason"),
                      str(c["score"]), b.refs.cell(c.get("evidence_ids"))] for c in rows],
                    [0.6, 2.0, 1.8, 3.0, 0.7, 1.3])
        b.p(f"The exploratory track is summarised in Table {n} ({len(exploratory)} candidates in total).")
    else:
        b.p("No exploratory candidate was found.")

    b.h2("3.5 Co-mutations without a targeted drug")
    co = ev.get("co_mutations") or []
    patterns = ev.get("co_mutation_patterns") or []
    b.p((f"{_and([v.get('notation') for v in co])} had no targeted drug candidate. " if co
         else "Every tiered variant has variant-level drug evidence. ")
        + (f"Known co-mutation patterns present: {'; '.join(patterns)}." if patterns else ""))

    b.h1("4\tDiscussion")
    b.discussion(["primary", "literature", "biology", "hypothesis"],
                 "Section 4 was written by language-model agents ({models}) from the results above and is reproduced "
                 "without editing, apart from citation numbers.")

    b.h1("5\tLimitations")
    for line in [*(ev.get("limitations") or []),
                 "The discussion is machine-generated. Its citations were validated automatically, but it was not "
                 "reviewed by an expert."]:
        b.p(line)
    b.references()

    b.h1("Appendix A: Variants excluded before tiering")
    audit = variants.get("qc_audit") or []
    if audit:
        b.table("Variants removed by quality control, germline filtering or consequence, with the reason.",
                ["Step", "Variant", "Position", "ALT / depth", "Reason"],
                [[DROP_STEPS.get(v.get("dropped_at"), v.get("dropped_at")),
                  f"{v['gene']} {v.get('protein_change') or ''}".strip() if v.get("gene") else None,
                  f"chr{v.get('chrom')}:{v.get('pos')} {v.get('ref')}>{v.get('alt')}",
                  f"{v.get('alt_reads')}/{v.get('depth')}" if v.get("depth") is not None else None, v.get("reason")]
                 for v in audit], [1.3, 1.5, 2.4, 1.1, 3.1])
    else:
        b.p("No variant was removed.")
    b.provenance("Appendix B: Data provenance", [
        ["Input file", f"{inp.get('filename') or sample} ({(inp.get('format') or 'vcf').upper()}, "
                       f"{inp.get('assembly') or 'GRCh38'})"],
        ["Cancer type", f"{cancer_name} (OncoTree {code}"
                        + (f"; lineage {' → '.join(cancer['lineage'])}" if cancer.get("lineage") else "") + ")"],
        *([["Follow-up question", q["followup_question"]]] if q.get("followup_question") else []),
    ])
    return (f"{sample}: precision oncology research report", f"{genes[0] if genes else 'VCF'}_{code}")


# ---------- Packaging ----------

def build_report(query, figure=None, figure_note=None):
    """query: Query.to_dict(). figure: PNG bytes for Figure 1 (the network), or None.
    Returns (docx bytes, file name)."""
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
