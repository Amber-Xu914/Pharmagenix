"""Workflow 4: patient VCF + cancer type -> candidate drugs, run on the synthetic mock VCFs (no network: VEP and the
other live sources are stubbed with fixed annotations)."""

from io import BytesIO

import pytest

from app.errors import APIError
from app.extensions import db
from app.reference.importers import replace_tables
from app.reference.importers.civic import build_evidence, parse_profile
from app.reference.importers.hgnc import parse_hgnc
from app.reference.importers.hotspots import build_hotspots
from app.reference.importers.intogen import build_drivers
from app.reference.models import (
    CancerHotspot, CivicEvidence, Gene, GeneAlias, IntogenDriver, SourceRelease, SubtypeGeneFrequency,
)
from app.services.variants.cancer_types import disease_match, resolve_cancer_type
from app.services.variants.matching import match_profile
from app.services.variants.oncokb import from_oncokb
from app.services.variants.cancer_types import oncokb_in_subtype
from app.services.variants.parse import parse_maf, parse_upload, parse_vcf, short_protein
from app.services.vcf_explorer import load_example, mock_examples

# What Ensembl VEP returns for the mock VCFs' variants (GRCh38), keyed by (chrom, pos).
VEP = {
    ("7", 55191822): ("EGFR", "missense_variant", "missense", "L858R", 21, {"alphamissense": 0.997}),
    ("7", 55181378): ("EGFR", "missense_variant", "missense", "T790M", 20, {"alphamissense": 0.95}),
    ("7", 55181399): ("EGFR", "missense_variant", "missense", "C797S", 20, {"alphamissense": 0.9}),
    ("7", 55181370): ("EGFR", "synonymous_variant", "synonymous", None, 20, {"gnomad_af": 0.045}),
    ("7", 55174062): ("EGFR", "intron_variant", "noncoding", None, None, {}),
    ("7", 55174772): ("EGFR", "inframe_deletion", "inframe_deletion", "E746_A750del", 19, {}),
    ("17", 7673802): ("TP53", "missense_variant", "missense", "R273H", 8, {"alphamissense": 0.99}),
    ("17", 7675088): ("TP53", "missense_variant", "missense", "R175H", 5, {"alphamissense": 0.99}),
    ("17", 7676154): ("TP53", "missense_variant", "missense", "P72R", 4, {"gnomad_af": 0.72}),
    ("19", 1207022): ("STK11", "stop_gained", "nonsense", "Q37*", 1, {}),
    ("19", 10491664): ("KEAP1", "missense_variant", "missense", "R413L", 3, {"alphamissense": 0.98}),
    ("2", 140883985): ("LRP1B", "missense_variant", "missense", "L1334P", 25, {"alphamissense": 0.2}),
    ("9", 21971121): ("CDKN2A", "stop_gained", "nonsense", "R80*", 2, {}),
    ("12", 25245351): ("KRAS", "missense_variant", "missense", "G12C", 2, {"alphamissense": 0.99}),
    ("19", 10491905): ("KEAP1", "missense_variant", "missense", "G333C", 3, {"alphamissense": 0.95}),
}


def civic_row(eid, profile, therapies, significance, level, disease="Lung Non-small Cell Carcinoma", doid="3908",
              direction="Supports", interaction=""):
    return {"evidence_id": str(eid), "molecular_profile": profile, "molecular_profile_id": "1", "disease": disease,
            "doid": doid, "therapies": therapies, "therapy_interaction_type": interaction,
            "evidence_type": "Predictive", "evidence_direction": direction, "evidence_level": level,
            "significance": significance, "evidence_statement": f"Statement {eid}.", "citation_id": str(1000 + eid),
            "source_type": "PubMed", "citation": "Someone et al.", "rating": "4", "evidence_status": "accepted",
            "variant_origin": "Somatic", "evidence_civic_url": f"https://civicdb.org/links/evidence_items/{eid}"}


CIVIC_ROWS = [
    civic_row(1, "EGFR L858R", "Osimertinib", "Sensitivity/Response", "A", "Lung Adenocarcinoma", "3910"),
    civic_row(2, "EGFR L858R", "Gefitinib,Erlotinib", "Sensitivity/Response", "A", interaction="Substitutes"),
    civic_row(3, "EGFR T790M", "Gefitinib", "Resistance", "A"),
    civic_row(4, "EGFR T790M", "Osimertinib", "Sensitivity/Response", "A"),
    civic_row(5, "EGFR Mutation", "Cetuximab", "Sensitivity/Response", "C", "Colorectal Cancer", "9256"),
    civic_row(6, "EGFR L858R", "Neratinib", "Sensitivity/Response", "D"),
    civic_row(7, "EML4::ALK Fusion AND ALK G1202R", "Lorlatinib", "Sensitivity/Response", "B"),
    civic_row(8, "EGFR C797S", "Osimertinib", "Resistance", "B"),
    civic_row(9, "EGFR Exon 19 Deletion", "Afatinib", "Sensitivity/Response", "A"),
    civic_row(10, "TP53 R273H", "Methotrexate", "Resistance", "D", "Breast Cancer", "1612"),
    civic_row(11, "STK11 Mutation", "Pembrolizumab", "Resistance", "B", "Lung Adenocarcinoma", "3910"),
]
HGNC_ROWS = [{"hgnc_id": f"HGNC:{i}", "symbol": s, "name": f"{s} gene", "status": "Approved", "alias_symbol": "",
              "prev_symbol": "", "entrez_id": str(i), "ensembl_gene_id": "", "uniprot_ids": "", "date_modified": "2026"}
             for i, s in enumerate(["EGFR", "TP53", "STK11", "KEAP1", "LRP1B", "CDKN2A"], start=1)]


def driver(symbol, pct, role="Act", cancer="LUAD"):
    return {"SYMBOL": symbol, "CANCER_TYPE": cancer, "COHORT": f"TCGA_WXS_{cancer}", "ROLE": role, "SAMPLES": "10",
            "%_SAMPLES_COHORT": str(pct), "QVALUE_COMBINATION": "0", "IS_DRIVER": "True"}


INTOGEN_ROWS = [driver("EGFR", 0.46), driver("TP53", 0.49, "LoF"), driver("STK11", 0.14, "LoF"),
                driver("KEAP1", 0.19, "LoF"), driver("LRP1B", 0.31, "LoF"), driver("TP53", 0.79, "LoF", "LUSC")]
HOTSPOTS = [{"hugoSymbol": "TP53", "residue": "R273", "aminoAcidPosition": "{'start': 273, 'end': 273}",
             "type": "single residue", "tumorCount": 609, "variantAminoAcid": "{'H': 300, 'C': 250}"},
            {"hugoSymbol": "EGFR", "residue": "745-759", "aminoAcidPosition": None, "type": "in-frame indel",
             "tumorCount": 900, "variantAminoAcid": "{}"}]


# cBioPortal mutation frequencies (R); LRP1B is set low here so it falls below the Tier 3 threshold.
FREQUENCIES = {"EGFR": 0.14, "TP53": 0.49, "STK11": 0.14, "KEAP1": 0.19, "LRP1B": 0.05}


@pytest.fixture
def vcf_reference(app):
    genes, aliases, _ = parse_hgnc(HGNC_ROWS)
    replace_tables([(Gene, genes), (GeneAlias, aliases)])
    replace_tables([(CivicEvidence, build_evidence(CIVIC_ROWS)), (IntogenDriver, build_drivers(INTOGEN_ROWS)),
                    (CancerHotspot, build_hotspots(HOTSPOTS)),
                    (SubtypeGeneFrequency, [{"oncotree_code": "LUAD", "study_id": "luad_test", "gene_symbol": g,
                                             "altered": 1, "profiled": 1, "frequency": f}
                                            for g, f in FREQUENCIES.items()])])
    for source in ("hgnc", "civic", "intogen", "hotspots", "cbioportal"):
        db.session.add(SourceRelease(source=source, version=f"{source} test", status="ok"))
    db.session.commit()


@pytest.fixture
def stub_live(monkeypatch):
    """VEP answers from the table above; DGIdb and PubTator return fixed results."""
    calls = {"vep": 0, "annotated": []}

    def vep(self, variants, fill_only=False):
        calls["vep"] += 1
        for v in variants:
            calls["annotated"].append((v["chrom"], v["pos"]))
            gene, term, cls, protein, exon, extra = VEP[(v["chrom"], v["pos"])]
            v.update(gene=gene, consequence=term, classification=cls, protein_change=protein, exon=exon,
                     hgvsc=None, hgvsp=protein and f"p.{protein}", clinvar=[], gnomad_af=None, alphamissense=None,
                     revel=None, cadd_phred=None)
            v.update(extra)
        return len(variants)

    monkeypatch.setattr("app.services.variants.vep.VEPClient.annotate", vep)
    monkeypatch.setattr("app.services.biomed.dgidb.DGIdbClient.fetch_interactions", lambda self, **kw: [
        {"gene": "EGFR", "drug": "ERLOTINIB", "interaction_types": ["inhibitor"], "score": 3.0, "sources": ["TTD"],
         "pmids": [], "drug_approved": True}])
    monkeypatch.setattr("app.services.biomed.pubtator.PubTatorClient.search", lambda self, text, limit=5: [
        {"pmid": 111, "title": f"Paper about {text}", "journal": "J", "year": "2024", "authors": [], "query": text}])
    monkeypatch.setattr("app.services.biomed.pubtator.PubTatorClient.abstracts", lambda self, pmids: {
        111: {"title": "Paper", "abstract": "We studied tumour samples from many patients. Mutant lung cancer cells "
              "responded to the inhibitor in this study.", "gene_ids": set(), "chemicals": set()}})
    return calls


def run(example_id, cancer=None):
    from app.services.variants.pipeline import run_pipeline

    variants, info, example = load_example(example_id)
    return run_pipeline(variants, info, resolve_cancer_type(cancer or example["cancer_type"]))


# ---------- parsing ----------

def test_short_protein():
    assert short_protein("ENSP00000275493.2:p.Leu858Arg") == "L858R"
    assert short_protein("p.Glu746_Ala750del") == "E746_A750del"
    assert short_protein("p.Trp1038CysfsTer12") == "W1038fs"
    assert short_protein("p.R213X") == "R213*"
    assert short_protein("p.Leu861=") is None


def test_mock_vcfs_and_read_metrics():
    assert {e["id"] for e in mock_examples()} >= {"mock_luad_egfr_l858r_t790m", "mock_luad_tier3_only"}
    variants, info, _ = load_example("mock_luad_egfr_l858r_t790m")
    assert (info["annotation"], info["assembly"], info["tumour_sample"], info["normal_sample"]) == (
        None, "GRCh38", "TUMOR", "NORMAL")
    l858r = next(v for v in variants if v["pos"] == 55191822)
    assert (l858r["depth"], l858r["alt_reads"], l858r["vaf"], l858r["normal_alt_reads"]) == (160, 56, 0.35, 0)


def test_parse_maf_and_errors():
    header = "Hugo_Symbol\tChromosome\tStart_Position\tReference_Allele\tTumor_Seq_Allele2\tVariant_Classification\t" \
             "HGVSp_Short\tExon_Number\tTumor_Sample_Barcode\tNCBI_Build\tt_alt_count\tt_ref_count\tn_alt_count"
    row = "EGFR\t7\t55242465\tGGAATTAAGAGAAGC\t-\tIn_Frame_Del\tp.E746_A750del\t19/28\tS1\tGRCh37\t10\t30\t0"
    variants, info = parse_maf(f"{header}\n{row}\n")
    v = variants[0]
    assert (v["protein_change"], v["classification"], v["exon"], v["depth"], v["vaf"]) == (
        "E746_A750del", "inframe_deletion", 19, 40, 0.25)
    with pytest.raises(APIError, match="2 tumour samples"):
        parse_maf(f"{header}\n{row}\n{row.replace('S1', 'S2')}\n")
    with pytest.raises(APIError):
        parse_upload("not a variant file")


def test_hotspot_import():
    rows = build_hotspots(HOTSPOTS)
    assert [(r["gene_symbol"], r["start"], r["end"], r["hotspot_type"]) for r in rows] == [
        ("TP53", 273, 273, "single residue"), ("EGFR", 745, 759, "in-frame indel")]
    assert rows[0]["variant_amino_acids"] == {"H": 300, "C": 250}


# ---------- matching and cancer types ----------

def _v(vid, gene, protein, cls="missense", exon=None, intron=None):
    return {"id": vid, "gene": gene, "protein_change": protein, "classification": cls, "exon": exon, "intron": intron}


def test_match_profile():
    l858r, del19 = _v("V1", "EGFR", "L858R", exon=21), _v("V2", "EGFR", "E746_A750del", "inframe_deletion", 19)
    g12c, met = _v("V3", "KRAS", "G12C"), _v("V4", "MET", None, "splice", intron=14)
    assert match_profile(parse_profile("EGFR L858R"), [l858r]) == ("exact", ["V1"])
    assert match_profile(parse_profile("EGFR Exon 19 Deletion"), [del19]) == ("exon", ["V2"])
    assert match_profile(parse_profile("KRAS G12/G13"), [g12c]) == ("codon", ["V3"])
    assert match_profile(parse_profile("EGFR L858R OR EGFR Exon 19 Deletion"), [l858r, del19])[0] == "exact"
    assert match_profile(parse_profile("EGFR Mutation"), [l858r]) == ("gene", ["V1"])
    assert match_profile(parse_profile("MET Exon 14 Skipping Mutation"), [met]) == ("exon", ["V4"])
    assert match_profile(parse_profile("EML4::ALK Fusion AND ALK G1202R"), [_v("V5", "ALK", "G1202R")]) is None
    assert match_profile(parse_profile("EGFR Amplification"), [l858r]) is None


def test_cancer_types(app):
    luad = resolve_cancer_type("lung adenocarcinoma")  # OncoTree not imported: built-in lung types
    assert (luad["code"], luad["lineage"]) == ("LUAD", ["LUAD", "NSCLC", "LUNG"])
    # free text in the combobox: codes, names, aliases and the "Name (CODE)" suggestions all resolve
    for typed in ("luad", "  Lung  Adenocarcinoma ", "Lung Adenocarcinoma (LUAD)", "adenocarcinoma"):
        assert resolve_cancer_type(typed)["code"] == "LUAD"
    assert resolve_cancer_type("non-small cell lung cancer")["code"] == "NSCLC"
    assert disease_match("3910", luad) == 3 and disease_match("3908", luad) == 3  # LUAD, NSCLC
    assert disease_match("5409", luad) == 2 and disease_match("9256", luad) == 1  # SCLC, colorectal
    with pytest.raises(APIError, match="lung cancer only"):
        resolve_cancer_type("breast cancer")


def test_oncokb_parsing():
    data = {"treatments": [
        {"drugs": [{"drugName": "Osimertinib"}], "level": "LEVEL_1", "pmids": ["123"],
         "levelAssociatedCancerType": {"code": "NSCLC"}},
        {"drugs": [{"drugName": "Gefitinib"}], "level": "LEVEL_R1", "pmids": []}]}
    rows = from_oncokb(data, "EGFR", "T790M")
    assert [(r["drug"], r["significance"]) for r in rows] == [("Osimertinib", "Sensitivity/Response"),
                                                              ("Gefitinib", "Resistance")]


def test_oncokb_cancer_type_matching(app, vcf_reference):
    # OncoKB attaches most lung levels to the main type: empty code and name, only mainType.name is set.
    main_type = {"drugs": [{"drugName": "Osimertinib"}], "level": "LEVEL_1",
                 "levelAssociatedCancerType": {"code": "", "name": "", "mainType": {"name": "Non-Small Cell Lung Cancer"}}}
    by_code = {"drugs": [{"drugName": "X"}], "level": "LEVEL_1", "levelAssociatedCancerType": {"code": "LUSC"}}
    melanoma = {"drugs": [{"drugName": "Y"}], "level": "LEVEL_1",
                "levelAssociatedCancerType": {"code": "", "mainType": {"name": "Melanoma"}}}
    rows = from_oncokb({"treatments": [main_type, by_code, melanoma]}, "EGFR", "L858R")
    luad, lusc, sclc = (resolve_cancer_type(c) for c in ("LUAD", "LUSC", "SCLC"))
    assert [oncokb_in_subtype(r, luad) for r in rows] == [True, False, False]
    assert [oncokb_in_subtype(r, lusc) for r in rows] == [True, True, False]
    assert not oncokb_in_subtype(rows[0], sclc)


# ---------- the deterministic tools on the mock VCFs ----------

def test_qc_tiering_and_tracks(app, vcf_reference, stub_live):
    bundle = run("mock_luad_egfr_l858r_t790m")
    variants = bundle["variants"]

    # Stage 1: every artefact is in the QC audit table with its reason; VEP only sees variants that passed the reads.
    reasons = {(d["chrom"], d["pos"]): d["reason"] for d in variants["qc_audit"]}
    assert reasons[("12", 25245350)] == "depth 12 < 20"
    assert reasons[("7", 140753336)] == "tumour VAF 2.9% < 5%"
    assert reasons[("12", 25245351)] == "FILTER is weak_evidence, not PASS"
    assert reasons[("17", 7676154)].startswith("germline: present in the matched normal")
    assert reasons[("7", 55174062)] == "not protein-altering (intron_variant)"
    assert ("12", 25245350) not in stub_live["annotated"] and ("17", 7676154) not in stub_live["annotated"]
    assert variants["counts"]["dropped"] == {"qc": 3, "germline": 2, "consequence": 1}

    # Stage 3: tiers, and low priority (kept, not deleted)
    tiers = {v["notation"]: v for v in variants["tiered"]}
    assert (tiers["EGFR p.L858R"]["tier"], tiers["EGFR p.T790M"]["tier"], tiers["TP53 p.R273H"]["tier"]) == (1, 1, 2)
    # A truncating variant in a tumour suppressor matches gene-level evidence ("STK11 Mutation") as a class:
    # STK11 p.Q37* is Tier 1 in LUAD (feedback: it used to fall to Tier 3)
    stk11 = tiers["STK11 p.Q37*"]
    assert stk11["tier"] == 1 and "STK11 Mutation: truncating variant in a tumour suppressor" in stk11["tier_reason"]
    [low] = variants["low_priority"]
    assert low["notation"] == "LRP1B p.L1334P" and low["tier3"]["S"] == 0.26 and low["reason"].startswith("low priority")
    assert low["tier3"]["B_parts"] == {"cancer_role": 0.4, "pathway": 0, "tsg_loss": 0}

    # Stages 5 and 7: two tracks, ranked separately; resistance down-ranks within its track
    tracks = bundle["tracks"]
    assert [(c["drug"], c["score"]) for c in tracks["established"]] == [
        ("Osimertinib", 97), ("Erlotinib", 95), ("Gefitinib", 35)]
    gefitinib = tracks["established"][2]
    assert gefitinib["strong_resistance"] and gefitinib["co_occurrence"] == [
        "response evidence for EGFR p.L858R, but the co-occurring EGFR p.T790M confers resistance"]
    erlotinib = tracks["established"][1]
    assert {g["source"] for g in erlotinib["gene_links"]} == {"DGIdb"}  # the DGIdb record merged into the same drug
    assert [(c["drug"], c["score"]) for c in tracks["exploratory"]] == [("Neratinib", 65), ("Cetuximab", 33)]
    assert tracks["exploratory"][0]["literature"][0]["pmid"] == 111  # literature support +5
    assert "Methotrexate" not in str(tracks) and "Lorlatinib" not in str(tracks)  # unsupported / fusion-only
    assert [c["drug"] for c in bundle["resistance_only"]] == ["Pembrolizumab"]  # STK11 loss: drug to avoid
    assert {v["notation"] for v in bundle["co_mutations"]} == {"TP53 p.R273H", "STK11 p.Q37*"}
    assert "no_targeted_therapy" not in bundle["outcome"]
    resist = next(e for e in bundle["network"]["edges"] if e["data"]["category"] == "resistant")
    assert resist["data"]["source"] == f"variant:{tiers['EGFR p.T790M']['id']}"  # from the variant that causes it


def test_t790m_with_c797s_down_ranks_osimertinib(app, vcf_reference, stub_live):
    established = run("mock_luad_egfr_t790m_c797s")["tracks"]["established"]
    assert [c["drug"] for c in established] == ["Erlotinib", "Osimertinib", "Gefitinib"]
    osimertinib = established[1]
    assert osimertinib["strong_resistance"] and "EGFR p.C797S" in osimertinib["track_reason"]
    assert any("co-occurring EGFR p.C797S" in n for n in osimertinib["co_occurrence"])


def test_no_approved_targeted_therapy(app, vcf_reference, stub_live):
    bundle = run("mock_luad_egfr_t790m_c797s", cancer="SCLC")  # NSCLC evidence is not subtype-specific for SCLC
    assert bundle["tracks"]["established"] == []
    assert bundle["outcome"]["no_targeted_therapy"].startswith("No approved targeted therapy")


def test_tier3_only_continues_downstream(app, vcf_reference, stub_live):
    bundle = run("mock_luad_tier3_only")
    assert bundle["outcome"]["status"] == "found"  # no decision point: every tiered variant goes on
    [keap1] = bundle["variants"]["tiered"]
    t = keap1["tier3"]
    assert keap1["notation"] == "KEAP1 p.R413L" and keap1["tier"] == 3
    assert (t["F"], t["R"], t["B"], t["S"]) == (0.98, 0.76, 0.7, 0.83)  # 0.4·0.98 + 0.3·0.76 + 0.3·0.7
    assert t["B_parts"] == {"cancer_role": 0.4, "pathway": 0.3, "tsg_loss": 0} and t["pathways"] == ["NRF2"]
    assert keap1["literature"][0]["pmid"] == 111  # literature search for Tier 3; the tier is unchanged
    assert bundle["source_status"]["dgidb"]["state"] == "ok"
    assert bundle["outcome"]["no_targeted_therapy"]


def test_tsg_truncation_is_tier2_without_evidence(app, vcf_reference, stub_live):
    """A truncating variant in a tumour suppressor driver of the subtype is at least Tier 2, like a hotspot."""
    db.session.execute(db.delete(CivicEvidence).where(CivicEvidence.gene_symbol == "STK11"))
    stk11 = next(v for v in run("mock_luad_kras_g12c")["variants"]["tiered"] if v["gene"] == "STK11")
    assert stk11["tier"] == 2 and "tumour suppressor driver in LUAD" in stk11["tier_reason"]


def test_gene_level_evidence_does_not_tier_a_missense(app, vcf_reference, stub_live):
    """"EGFR Mutation" (gene level) must not lift an EGFR missense; only truncating TSG variants match as a class."""
    from app.services.variants.pipeline import civic_matches

    missense = {"id": "V1", "gene": "EGFR", "protein_change": "A289V", "classification": "missense", "exon": 7}
    [match] = civic_matches([missense], resolve_cancer_type("LUAD"))
    assert (match["molecular_profile"], match["specificity"]) == ("EGFR Mutation", "gene")


def test_nothing_tiered(client, vcf_reference, stub_live, fake_sources):
    vcf = ("##fileformat=VCFv4.2\n##reference=GRCh38\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tTUMOR\n"
           "chr7\t55181370\t.\tG\tA\t.\tPASS\t.\tGT:AD:DP\t0/1:50,30:80\n")
    form = {"file": (BytesIO(vcf.encode()), "mine.vcf"), "cancer_type": "LUAD", "test_data": "1"}
    data = client.post("/api/vcf/explore", data=form, content_type="multipart/form-data").get_json()
    assert data["evidence"]["outcome"]["status"] == "none"
    assert data["evidence"]["variants"]["qc_audit"][0]["reason"] == "germline: gnomAD AF 4.5% > 1%"
    assert data["reports"] == []  # nothing tiered: no LLM call


def test_pipeline_needs_reference_data(app, stub_live):
    with pytest.raises(RuntimeError, match="import-data civic intogen hotspots cbioportal"):
        run("mock_luad_egfr_l858r_t790m")


# ---------- agents, API and follow-up ----------

def test_agent_statement_check():
    from app.services.variants.agents import statement_check

    assert statement_check("## Literature\n- STK11 loss is reported in KL tumours [E4].\n"
                           "- Hypothesis: STK11 loss may reduce response to drug X.") == []
    assert statement_check("- Hypothesis: KEAP1 loss sensitises to drug X [E2]. Test it in isogenic LUAD cells.\n"
                           "- STK11 p.Q37*: no retrieved paper reports this variant.") == []
    assert statement_check("- No Tier 3 variants are listed, so there are no papers to assess.\n"
                           "These are exploratory connections, not treatment recommendations.") == []
    assert statement_check("- STK11 loss clearly predicts a poor response to everything.") == [
        "1 statement(s) neither cite evidence nor are labelled as a hypothesis"]


class FakeLLM:
    enabled, model = True, "fake"

    def __init__(self, prompts):
        self.prompts = prompts

    def complete(self, system, user):
        from app.services.llm import LLMResult

        self.prompts.append((system, user))
        return LLMResult(text="## Overview\nOsimertinib leads the established track [E1, E3].", model="fake",
                         input_tokens=1, output_tokens=1, latency_ms=1)


def test_vcf_api_agents_and_followup(client, vcf_reference, stub_live, monkeypatch):
    prompts = []
    monkeypatch.setattr("app.services.llm.report_generator.get_llm_client", lambda provider: FakeLLM(prompts))

    data = client.post("/api/vcf/explore", json={"example": "mock_luad_egfr_l858r_t790m"}).get_json()
    assert (data["entity_type"], data["entities"]) == ("vcf", ["mock_luad_egfr_l858r_t790m"])
    roles = {r["role"]: r for r in data["reports"]}
    assert set(roles) == {"primary", "literature", "biology", "hypothesis"}
    assert roles["primary"]["template_version"] == 3 and roles["literature"]["template_version"] is None
    assert all(r["status"] == "success" for r in data["reports"])

    analysis = next(user for system, user in prompts if system.startswith("You are the Analysis agent"))
    assert "A. Established track" in analysis and "#1 Osimertinib | score 97" in analysis
    assert "## B. Exploratory predictions" in analysis
    hypothesis = next(user for system, user in prompts if system.startswith("You are the Hypothesis agent"))
    assert "Tier 1 STK11 p.Q37*" in hypothesis
    assert "#1 Neratinib" in hypothesis and "Osimertinib" not in hypothesis.split("Evidence (")[0]  # exploratory only

    followup = client.post(f"/api/queries/{data['id']}/ask", json={"followup_question": "Why not gefitinib?"})
    assert followup.status_code == 201 and followup.get_json()["evidence"] == data["evidence"]
    assert len(followup.get_json()["reports"]) == 4 and "Why not gefitinib?" in prompts[-1][1]


def test_vcf_upload_needs_test_data_confirmation(client, vcf_reference, stub_live, fake_sources):
    from app.services.vcf_explorer import mock_path

    _, path = mock_path("mock_luad_tier3_only")
    form = {"file": (BytesIO(path.read_bytes()), "mine.vcf"), "cancer_type": "LUAD"}
    assert client.post("/api/vcf/explore", data=form, content_type="multipart/form-data").status_code == 400


def test_vcf_options_and_example_download(client):
    data = client.get("/api/vcf/options").get_json()
    assert "LUAD" in {t["code"] for t in data["cancer_types"]}
    assert data["ready"] is False and data["qc"]["min_depth"] == 20
    resp = client.get(f"/api/vcf/examples/{data['examples'][0]['id']}")
    assert resp.status_code == 200 and resp.data.startswith(b"##fileformat=VCF")
    assert client.get("/api/vcf/examples/nope").status_code == 400


def test_vep_outage_is_a_clear_503(app, vcf_reference, stub_live, monkeypatch):
    from app.errors import APIError

    def down(self, variants, fill_only=False):
        raise RuntimeError("503 Server Error: Service Unavailable for url: https://rest.ensembl.org/vep/x")

    monkeypatch.setattr("app.services.variants.vep.VEPClient.annotate", down)
    with pytest.raises(APIError) as caught:
        run("mock_luad_egfr_l858r_t790m")
    assert caught.value.status == 503
    assert "Ensembl VEP" in caught.value.message and "(503 Service Unavailable)" in caught.value.message


# ---------- AlphaGenome in-silico mutagenesis (no live calls: the scoring step is stubbed) ----------

def test_ism_is_off_without_a_key(client):
    data = client.get("/api/vcf/options").get_json()
    assert data["ism"]["enabled"] is False and "ALPHAGENOME_API_KEY" in data["ism"]["reason"]
    resp = client.post("/api/vcf/ism", json={"chrom": "7", "pos": 55191822})
    assert resp.status_code == 503


def test_ism_route_validates_and_caches(app, client, monkeypatch):
    from app.services.variants import alphagenome

    app.config["ALPHAGENOME_API_KEY"] = "test-key"
    monkeypatch.setattr(alphagenome, "_installed", lambda: True)
    monkeypatch.setattr(alphagenome, "_cache", alphagenome.OrderedDict())
    calls = []

    def fake_score(chrom, pos, gene, output, tissue, flank, length):
        calls.append((chrom, pos, gene, output, length))
        positions = list(range(pos - flank, pos + flank + 1))
        scores = {b: [0.1] * len(positions) for b in "ACGT"}
        return {"chrom": chrom, "positions": positions, "ref": ["T"] * len(positions), "scores": scores,
                "max_abs": 0.1, "output": output, "tissue": tissue, "tissue_matched": True, "tracks": 2,
                "gene": gene, "flank": flank, "model": "stub"}

    monkeypatch.setattr(alphagenome, "_score", fake_score)
    body = {"chrom": "7", "pos": 55191822, "ref": "T", "alt": "G", "gene": "EGFR"}
    first = client.post("/api/vcf/ism", json=body).get_json()
    assert first["chrom"] == "chr7" and len(first["positions"]) == 21 and first["cached"] is False
    assert first["variant"] == {"pos": 55191822, "ref": "T", "alt": "G", "snv": True}
    assert client.post("/api/vcf/ism", json=body).get_json()["cached"] is True
    assert calls == [("chr7", 55191822, "EGFR", "RNA_SEQ", 2**20)]

    assert client.post("/api/vcf/ism", json={**body, "assembly": "GRCh37"}).status_code == 400
    assert client.post("/api/vcf/ism", json={**body, "chrom": "chrM"}).status_code == 400
    assert client.post("/api/vcf/ism", json={"chrom": "7"}).status_code == 400


def test_ism_scalar_picks_the_gene_and_tissue():
    import numpy as np
    import pandas as pd
    from types import SimpleNamespace
    from app.services.variants.alphagenome import _scalar

    adata = SimpleNamespace(
        X=np.array([[1.0, 3.0, 5.0], [10.0, 20.0, 30.0]]),
        obs=pd.DataFrame({"gene_name": ["EGFR", "SEC61G"]}),
        var=pd.DataFrame({"ontology_curie": ["UBERON:0002048", "UBERON:0002048", "UBERON:0000955"]}),
    )
    assert _scalar(adata, "EGFR", "UBERON:0002048") == (2.0, 2, True, "EGFR")
    # Unknown tissue: every track; unknown gene: the gene that moves most
    assert _scalar(adata, "KRAS", "CL:0000000") == (20.0, 3, False, "SEC61G")


def test_vcf_word_report(client, vcf_reference, stub_live, monkeypatch):
    import re
    import zipfile
    from io import BytesIO

    monkeypatch.setattr("app.services.llm.report_generator.get_llm_client", lambda provider: FakeLLM([]))
    data = client.post("/api/vcf/explore", json={"example": "mock_luad_egfr_l858r_t790m"}).get_json()
    resp = client.get(f"/api/queries/{data['id']}/report.docx")
    assert resp.status_code == 200 and "_EGFR_LUAD.docx" in resp.headers["Content-Disposition"]
    xml = zipfile.ZipFile(BytesIO(resp.data)).read("word/document.xml").decode()
    text = re.sub(r"<[^>]+>", "", xml)
    assert "Candidate therapies for a lung adenocarcinoma variant profile" in text
    for heading in ("Tiered variants.", "Established track", "Exploratory track", "Appendix A: Variants excluded",
                    "4.1 Analysis", "4.4 Hypotheses"):
        assert heading in text
    assert "Osimertinib" in text and "EGFR p.T790M" in text
    assert set(re.findall(r'w:anchor="(ref_\d+)"', xml)) <= set(re.findall(r'w:name="(ref_\d+)"', xml))
