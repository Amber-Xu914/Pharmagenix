"""The Word report download (services/report_docx.py): built from a stored query, no network or LLM calls."""

import base64
import re
import struct
import zipfile
import zlib
from io import BytesIO
from xml.dom import minidom

from app.extensions import db
from app.models import Query
from app.services.report_docx import DOCX_MIME, cite_runs, markdown_paragraphs, Refs, _restrictions


def png(width=4, height=3):
    """A valid PNG of the given size (white pixels)."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\xff" * width * 3 for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def open_docx(data):
    """Every XML part must parse; returns (zip, document text, document XML)."""
    z = zipfile.ZipFile(BytesIO(data))
    for name in z.namelist():
        if name.endswith((".xml", ".rels")):
            minidom.parseString(z.read(name))
    xml = z.read("word/document.xml").decode()
    return z, re.sub(r"<[^>]+>", "", xml), xml


def test_gene_report(client, fake_sources):
    query = client.post("/api/explore", json={"gene": "CYP2C19"}).get_json()
    resp = client.get(f"/api/queries/{query['id']}/report.docx")
    assert resp.status_code == 200 and resp.mimetype == DOCX_MIME
    assert f"Pharmagenix_report_PGX-{query['id']:05d}_CYP2C19.docx" in resp.headers["Content-Disposition"]

    z, text, xml = open_docx(resp.data)
    assert "Drug–Gene Interaction Explorer: Gene → Drugs Report" in text and "Brief Results" in text
    for heading in ("Abstract", "Introduction", "Methodology", "2.4 Evidence integration and grading", "Results",
                    "3.2 Evidence overview", "3.5 Drug safety", "Interpretation", "Limitations and data gaps",
                    "Conclusion", "References", "Methods and data sources", "Appendix A: Data provenance"):
        assert heading in text, heading
    assert "CYP2C19: drug–gene interaction report" in z.read("word/header1.xml").decode()
    # The LLM's [E1] / [E2] are renumbered to the reference list, and every link has its target
    assert "[E1]" not in text and "[E2]" not in text
    assert set(re.findall(r'w:anchor="(ref_\d+)"', xml)) <= set(re.findall(r'w:name="(ref_\d+)"', xml))
    assert "DGIdb interaction record: CYP2C19–Clopidogrel" in text
    assert "word/media/figure1.png" not in z.namelist()  # no figure sent


def test_report_with_network_figure(client, fake_sources):
    query = client.post("/api/explore", json={"gene": "CYP2C19"}).get_json()
    url = f"/api/queries/{query['id']}/report.docx"
    figure = "data:image/png;base64," + base64.b64encode(png(800, 600)).decode()
    resp = client.post(url, json={"figure": figure, "figure_shown": 60, "figure_total": 90})
    assert resp.status_code == 200
    z, text, _ = open_docx(resp.data)
    assert z.read("word/media/figure1.png") == png(800, 600)
    assert "rIdFigure1" in z.read("word/_rels/document.xml.rels").decode()
    assert "Figure 1. Interaction network of CYP2C19" in text and "Showing 60 of 90 relationships." in text

    assert client.post(url, json={"figure": "data:image/png;base64,not-base64!"}).status_code == 400
    assert client.post(url, json={"figure": base64.b64encode(b"GIF89a....").decode()}).status_code == 400


def test_report_needs_a_finished_search(app, client):
    pending = Query(entities=["TPMT"], entity_type="gene", relation_types=["snp"], status="pending")
    db.session.add(pending)
    db.session.commit()
    assert client.get(f"/api/queries/{pending.id}/report.docx").status_code == 400
    assert client.get("/api/queries/9999/report.docx").status_code == 404


def test_citations_and_markdown():
    refs = Refs([{"id": f"E{i}", "kind": "publication", "pmid": i, "title": "T"} for i in range(1, 8)])
    runs = "".join(cite_runs([1, 2, 3, 5]))
    assert re.sub(r"<[^>]+>", "", runs) == "[1–3, 5]" and 'w:anchor="ref_5"' in runs
    paras = markdown_paragraphs("# Title\n## Overview\n**Bold** claim [E3, E1].\n\n- range [E4–E6]\n- bad [E99]", refs)
    text = [re.sub(r"<[^>]+>", "", p) for p in paras]
    assert text == ["Overview", "Bold claim [1–2].", "•range [3–5]", "•bad [E99]"]  # numbered by first use
    assert refs.order == ["E3", "E1", "E4", "E5", "E6"]


def test_pbs_restriction_summary():
    assert _restrictions({"restrictions": []}) == "no restriction"
    assert _restrictions({"restrictions": [{"html": "<p>For prescribing by certain health practitioners</p>"}]}) \
        == "prescriber or quantity rules only"
    assert _restrictions({"restrictions": [{"html": "<h1>x</h1><p>Schizophrenia</p>"},
                                           {"html": "<p>Bipolar I disorder</p>"}, {"html": ""}]}) \
        == "Schizophrenia; Bipolar I disorder"
