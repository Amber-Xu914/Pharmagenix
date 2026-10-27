"""Generate the synthetic mock VCFs for Workflow 4 (no real patient data).

Each variant is written from transcript HGVS; Ensembl VEP's HGVS endpoint returns its exact GRCh38 position and
alleles, so the files annotate correctly. Read counts are invented to exercise every QC rule.

    cd backend && .venv/bin/python scripts/make_mock_vcfs.py
"""

import json
import time
from datetime import date
from pathlib import Path

import requests

OUT = Path(__file__).resolve().parent.parent / "app" / "mock_vcfs"
VEP_HGVS = "https://rest.ensembl.org/vep/human/hgvs"
GRCH38_LENGTHS = {"1": 248956422, "3": 198295559, "7": 159345973, "9": 138394717, "12": 133275309,
                  "13": 114364328, "17": 83257441, "19": 58617616, "2": 242193529}

# name -> transcript HGVS
V = {
    "EGFR L858R": "ENST00000275493:c.2573T>G",
    "EGFR T790M": "ENST00000275493:c.2369C>T",
    "EGFR C797S": "ENST00000275493:c.2390G>C",
    "EGFR E746_A750del": "ENST00000275493:c.2236_2250del",
    "EGFR Q787= (common SNP)": "ENST00000275493:c.2361G>A",
    "EGFR intronic": "ENST00000275493:c.2184+19G>A",
    "KRAS G12C": "ENST00000311936:c.34G>T",
    "KRAS G12D": "ENST00000311936:c.35G>A",
    "BRAF V600E": "ENST00000646891:c.1799T>A",
    "TP53 R273H": "ENST00000269305:c.818G>A",
    "TP53 R175H": "ENST00000269305:c.524G>A",
    "TP53 R248Q": "ENST00000269305:c.743G>A",
    "TP53 P72R (common SNP)": "ENST00000269305:c.215C>G",
    "STK11 Q37*": "ENST00000326873:c.109C>T",
    "KEAP1 G333C": "ENST00000171111:c.997G>T",
    "KEAP1 R413L": "ENST00000171111:c.1238G>T",
    "PIK3CA E545K": "ENST00000263967:c.1633G>A",
    "RB1 R320*": "ENST00000267163:c.958C>T",
    "CDKN2A R80*": "ENST00000304494:c.238C>T",
    "NFE2L2 R34G": "ENST00000397062:c.100C>G",
    "LRP1B L1334P": "ENST00000389484:c.4001T>C",
}

# Read support: (tumour depth, tumour ALT reads, normal depth, normal ALT reads, FILTER)
SOMATIC = (160, 56, 60, 0, "PASS")
SUBCLONAL = (180, 22, 70, 0, "PASS")
LOW_DEPTH = (12, 5, 40, 0, "PASS")          # DP < 20
LOW_VAF = (240, 7, 60, 0, "PASS")           # VAF 2.9 %
FAILED_FILTER = (90, 20, 50, 0, "weak_evidence")
GERMLINE = (150, 70, 80, 38, "PASS")        # present in the matched normal (and common in gnomAD)

SAMPLES = [
    ("mock_luad_egfr_l858r_t790m", "LUAD", "EGFR L858R + T790M (resistance to 1st-gen TKIs), with QC artefacts",
     [("EGFR L858R", SOMATIC), ("EGFR T790M", SUBCLONAL), ("TP53 R273H", SOMATIC), ("STK11 Q37*", SOMATIC),
      ("KRAS G12D", LOW_DEPTH), ("BRAF V600E", LOW_VAF), ("KRAS G12C", FAILED_FILTER),
      ("TP53 P72R (common SNP)", GERMLINE), ("EGFR Q787= (common SNP)", GERMLINE), ("EGFR intronic", SOMATIC),
      ("LRP1B L1334P", SOMATIC)]),
    ("mock_luad_egfr_t790m_c797s", "LUAD", "EGFR L858R + T790M + C797S (osimertinib resistance)",
     [("EGFR L858R", SOMATIC), ("EGFR T790M", SOMATIC), ("EGFR C797S", SUBCLONAL), ("TP53 R273H", SOMATIC)]),
    ("mock_luad_egfr_exon19del", "LUAD", "EGFR exon 19 deletion + TP53 / CDKN2A co-mutations",
     [("EGFR E746_A750del", SOMATIC), ("TP53 R175H", SOMATIC), ("CDKN2A R80*", SOMATIC)]),
    ("mock_luad_kras_g12c", "LUAD", "KRAS G12C + STK11 / KEAP1 co-mutations",
     [("KRAS G12C", SOMATIC), ("STK11 Q37*", SOMATIC), ("KEAP1 G333C", SOMATIC)]),
    ("mock_luad_braf_v600e", "LUAD", "BRAF V600E + TP53",
     [("BRAF V600E", SOMATIC), ("TP53 R248Q", SOMATIC)]),
    ("mock_lusc_no_targeted", "LUSC", "Squamous: PIK3CA / NFE2L2 / TP53, no approved targeted therapy",
     [("TP53 R248Q", SOMATIC), ("PIK3CA E545K", SOMATIC), ("NFE2L2 R34G", SOMATIC), ("CDKN2A R80*", SOMATIC)]),
    ("mock_sclc_tp53_rb1", "SCLC", "Small cell: TP53 + RB1, no approved targeted therapy",
     [("TP53 R273H", SOMATIC), ("RB1 R320*", SOMATIC)]),
    ("mock_luad_tier3_only", "LUAD", "No Tier 1–2 variant: only exploratory Tier 3 candidates",
     [("KEAP1 R413L", SOMATIC), ("LRP1B L1334P", SOMATIC), ("TP53 P72R (common SNP)", GERMLINE)]),
]


def locate(names):
    for attempt in range(6):  # Ensembl's REST servers return 503 now and then
        resp = requests.post(VEP_HGVS, params={"vcf_string": 1}, json={"hgvs_notations": [V[n] for n in names]},
                             headers={"Content-Type": "application/json", "Accept": "application/json"}, timeout=120)
        if resp.status_code not in (429, 502, 503):
            break
        time.sleep(3 * (attempt + 1))
    resp.raise_for_status()
    by_input = {r["input"]: r["vcf_string"] for r in resp.json()}
    out = {}
    for name in names:
        chrom, pos, ref, alt = by_input[V[name]].split("-")
        out[name] = (chrom, int(pos), ref, alt)
    return out


def sample_field(depth, alt):
    return f"{'0/1' if alt else '0/0'}:{depth - alt},{alt}:{alt / depth:.3f}:{depth}"


def write(name, variants, positions):
    chroms = sorted({positions[v][0] for v, _ in variants}, key=lambda c: int(c) if c.isdigit() else 99)
    lines = [
        "##fileformat=VCFv4.2",
        f"##fileDate={date.today():%Y%m%d}",
        "##source=Pharmagenix synthetic mock VCF (test data only; no real patient)",
        "##reference=GRCh38",
        *[f"##contig=<ID=chr{c},length={GRCH38_LENGTHS[c]}>" for c in chroms],
        "##FILTER=<ID=PASS,Description=\"All filters passed\">",
        "##FILTER=<ID=weak_evidence,Description=\"Mutation does not meet likelihood threshold\">",
        "##FORMAT=<ID=GT,Number=1,Type=String,Description=\"Genotype\">",
        "##FORMAT=<ID=AD,Number=R,Type=Integer,Description=\"Allelic depths for the ref and alt alleles\">",
        "##FORMAT=<ID=AF,Number=A,Type=Float,Description=\"Allele fraction of the alt allele\">",
        "##FORMAT=<ID=DP,Number=1,Type=Integer,Description=\"Read depth\">",
        "##normal_sample=NORMAL",
        "##tumor_sample=TUMOR",
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tNORMAL\tTUMOR",
    ]
    rows = []
    for variant, (t_depth, t_alt, n_depth, n_alt, flt) in variants:
        chrom, pos, ref, alt = positions[variant]
        rows.append((chrom, pos, f"chr{chrom}\t{pos}\t.\t{ref}\t{alt}\t.\t{flt}\t.\tGT:AD:AF:DP\t"
                                 f"{sample_field(n_depth, n_alt)}\t{sample_field(t_depth, t_alt)}"))
    rows.sort(key=lambda r: (int(r[0]) if r[0].isdigit() else 99, r[1]))
    (OUT / f"{name}.vcf").write_text("\n".join(lines + [r[2] for r in rows]) + "\n")


def main():
    OUT.mkdir(exist_ok=True)
    positions = locate(sorted({v for _, _, _, variants in SAMPLES for v, _ in variants}))
    manifest = []
    for name, cancer_type, description, variants in SAMPLES:
        write(name, variants, positions)
        manifest.append({"id": name, "file": f"{name}.vcf", "cancer_type": cancer_type, "label": description,
                         "variants": [v for v, _ in variants]})
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"wrote {len(manifest)} mock VCFs to {OUT}")


if __name__ == "__main__":
    main()
