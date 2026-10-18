from app.services.biomed.aggregator import build_evidence_items, build_network
from app.services.llm.report_generator import extract_citations
from app.services.relations import categorize, matches
from app.services.workflow.evidence import consolidate_pairs


def test_categorize_maps_dgidb_labels():
    assert categorize(["inhibitor"]) == ["inhibit"]
    assert categorize(["Agonist", "binder"]) == ["induce", "bind"]
    assert categorize([]) == ["other"]
    assert categorize(["something new"]) == ["other"]


def test_matches_relation_filter():
    assert matches(["inhibit"], ["inhibit", "bind"])
    assert not matches(["other"], ["inhibit"])
    assert matches(["other"], [])  # empty selection = everything


def test_extract_citations_flags_unknown_ids():
    used, invalid = extract_citations("x [E1]. y [E2, E9]; z [E1;E3]", {"E1", "E2", "E3"})
    assert used == ["E1", "E2", "E9", "E3"]
    assert invalid == ["E9"]


def test_evidence_table_and_network():
    interactions = [{"gene": "BRAF", "drug": "VEMURAFENIB", "interaction_types": ["inhibitor"],
                     "categories": ["inhibit"], "score": 3.14159, "sources": ["CIViC"], "pmids": [1, 2]}]
    variants = [{"variant": "rs113488022", "genes": ["BRAF"], "drugs": ["vemurafenib"], "level": "1A",
                 "phenotype_categories": ["Efficacy"], "diseases": [], "url": "u", "score": 1}]
    pubs = [{"pmid": 99, "title": "T", "journal": "J", "year": "2020", "authors": [], "query": "q"}]

    items = build_evidence_items(interactions, variants, [], pubs)
    assert [i["id"] for i in items] == ["E1", "E2", "E3"]
    assert items[0]["score"] == 3.142

    rows = consolidate_pairs(items)
    assert [(r["gene"], r["drug"]) for r in rows] == [("BRAF", "VEMURAFENIB")]  # DGIdb + PharmGKB merged
    assert rows[0]["categories"] == ["inhibit", "snp"]  # publications aren't table rows
    assert rows[0]["evidence_ids"] == ["E1", "E2"]
    assert (rows[0]["grade"], rows[0]["pgkb_level"]) == ("strong", "1A")

    net = build_network(items, ["BRAF"])
    node_ids = {n["data"]["id"] for n in net["nodes"]}
    assert node_ids == {"gene:BRAF", "drug:VEMURAFENIB", "variant:rs113488022"}
    assert next(n for n in net["nodes"] if n["data"]["id"] == "gene:BRAF")["data"]["is_query"]
    categories = sorted(e["data"]["category"] for e in net["edges"])
    assert categories == ["inhibit", "snp", "variant_of"]
