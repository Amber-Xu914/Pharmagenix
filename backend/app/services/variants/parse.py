"""Read a somatic VCF or MAF into one variant format.

A variant is a dict:
  chrom, pos, ref, alt, filter      genomic call (filter "PASS" when the file has no FILTER column)
  gene, consequence, classification HGNC symbol, SO term, and a coarse class (missense, frameshift, splice, ...)
  protein_change                    one-letter, without "p." (L858R, E746_A750del, W1038*); None when non-coding
  hgvsp, hgvsc, exon, intron        HGVS notation and exon / intron number (when the annotation has them)
  depth, alt_reads, vaf             tumour read depth, ALT reads and allele fraction (for QC)
  normal_depth, normal_alt_reads    the matched normal's reads (germline check)
  clinvar                           ClinVar significance terms
A VCF without annotations is annotated later by the Ensembl VEP REST API (vep.py); annotated ones are read as is.
"""

import csv
import io
import re

from ...errors import APIError

THREE_TO_ONE = {
    "Ala": "A", "Arg": "R", "Asn": "N", "Asp": "D", "Cys": "C", "Gln": "Q", "Glu": "E", "Gly": "G", "His": "H",
    "Ile": "I", "Leu": "L", "Lys": "K", "Met": "M", "Phe": "F", "Pro": "P", "Ser": "S", "Thr": "T", "Trp": "W",
    "Tyr": "Y", "Val": "V", "Ter": "*", "Sec": "U", "Xaa": "X",
}

# SO consequence terms, most severe first, and the coarse class each one maps to.
CONSEQUENCE_CLASSES = [
    ("transcript_ablation", "truncating"), ("splice_acceptor_variant", "splice"), ("splice_donor_variant", "splice"),
    ("stop_gained", "nonsense"), ("frameshift_variant", "frameshift"), ("stop_lost", "stop_lost"),
    ("start_lost", "start_lost"), ("inframe_insertion", "inframe_insertion"),
    ("inframe_deletion", "inframe_deletion"), ("protein_altering_variant", "missense"),
    ("missense_variant", "missense"), ("splice_donor_5th_base_variant", "splice_region"),
    ("splice_region_variant", "splice_region"), ("splice_donor_region_variant", "splice_region"),
    ("splice_polypyrimidine_tract_variant", "splice_region"), ("synonymous_variant", "synonymous"),
    ("stop_retained_variant", "synonymous"), ("start_retained_variant", "synonymous"),
]
SEVERITY = {term: i for i, (term, _) in enumerate(CONSEQUENCE_CLASSES)}
CLASS_OF = dict(CONSEQUENCE_CLASSES)
# Kept by QC (Workflow 4 stage 1): missense, nonsense, frameshift, splice site and in-frame indels.
PROTEIN_ALTERING = {"missense", "nonsense", "frameshift", "splice", "inframe_insertion", "inframe_deletion"}
TRUNCATING = {"nonsense", "frameshift", "splice"}

# MAF Variant_Classification -> coarse class
MAF_CLASSES = {
    "Missense_Mutation": "missense", "Nonsense_Mutation": "nonsense", "Frame_Shift_Del": "frameshift",
    "Frame_Shift_Ins": "frameshift", "In_Frame_Del": "inframe_deletion", "In_Frame_Ins": "inframe_insertion",
    "Splice_Site": "splice", "Translation_Start_Site": "start_lost", "Nonstop_Mutation": "stop_lost",
    "Splice_Region": "splice_region", "Silent": "synonymous",
}

GRCH38_CHR1 = 248956422
GRCH37_CHR1 = 249250621


# ---------- HGVS ----------

def short_protein(hgvsp):
    """'ENSP00000275493.2:p.Leu858Arg' or 'p.L858R' -> 'L858R'; 'p.Glu746_Ala750del' -> 'E746_A750del'."""
    if not hgvsp:
        return None
    text = hgvsp.split(":")[-1]
    text = text[2:] if text.startswith("p.") else text
    text = text.strip("()")
    if not text or text in ("=", "?") or text.endswith("="):
        return None
    for three, one in THREE_TO_ONE.items():
        text = text.replace(three, one)
    text = re.sub(r"fs\*?\d*$", "fs", text)  # W1038Cfs*12 -> W1038Cfs
    text = re.sub(r"^([A-Z]\d+)[A-Z]fs$", r"\1fs", text)
    return text.replace("X", "*") if re.fullmatch(r"[A-Z]\d+X", text) else text


def classify(consequences):
    """Most severe of a list of SO terms -> (term, class); class 'noncoding' when none is listed above."""
    terms = [t for t in consequences if t]
    known = sorted((t for t in terms if t in SEVERITY), key=SEVERITY.get)
    if known:
        return known[0], CLASS_OF[known[0]]
    return (terms[0] if terms else None), "noncoding"


def _number(value):
    """'19/28' -> 19."""
    match = re.match(r"(\d+)", str(value or ""))
    return int(match.group(1)) if match else None


# ---------- VCF ----------

def parse_vcf(text):
    """Returns (variants, info) where info = {"format": "vcf", "annotation": "CSQ" | "ANN" | "ANNOVAR" | None,
    "assembly": "GRCh38" | "GRCh37" | None, "tumour_sample": name}."""
    header, csq_fields, ann, columns = [], None, False, None
    rows = []
    for line in text.splitlines():
        if line.startswith("##"):
            header.append(line)
            if line.startswith("##INFO=<ID=CSQ"):
                fmt = re.search(r"Format: ([^\"]+)", line)
                csq_fields = fmt.group(1).strip().split("|") if fmt else None
            elif line.startswith("##INFO=<ID=ANN"):
                ann = True
        elif line.startswith("#CHROM"):
            columns = line[1:].rstrip("\n").split("\t")
        elif line.strip():
            rows.append(line.rstrip("\n").split("\t"))
    if not header or not header[0].startswith("##fileformat=VCF"):
        raise APIError("This is not a VCF file (the first line must be ##fileformat=VCF…)")
    if columns is None:
        raise APIError("The VCF has no #CHROM header line")

    tumour = _tumour_sample(header, columns)
    normal = _normal_sample(header, columns, tumour)
    annovar = any(r[7].find("AAChange.") >= 0 for r in rows[:50] if len(r) > 7)
    annotation = "CSQ" if csq_fields else "ANN" if ann else "ANNOVAR" if annovar else None
    variants = []
    for r in rows:
        if len(r) < 8:
            continue
        chrom, pos, _, ref, alts, _, flt, info = r[:8]
        info_map = dict(kv.split("=", 1) if "=" in kv else (kv, True) for kv in info.split(";"))
        sample = _sample_values(columns, r, tumour)
        normal_values = _sample_values(columns, r, normal)
        for i, alt in enumerate(alts.split(",")):
            depth, alt_reads = _reads(sample, i)
            normal_depth, normal_alt = _reads(normal_values, i)
            v = {"chrom": chrom.removeprefix("chr"), "pos": int(pos), "ref": ref, "alt": alt,
                 "filter": "PASS" if flt in (".", "", "PASS") else flt, "vaf": _vaf(sample, i),
                 "depth": depth, "alt_reads": alt_reads, "normal_depth": normal_depth, "normal_alt_reads": normal_alt}
            if annotation == "CSQ":
                v.update(_from_csq(info_map.get("CSQ"), csq_fields, alt))
            elif annotation == "ANN":
                v.update(_from_ann(info_map.get("ANN"), alt))
            elif annotation == "ANNOVAR":
                v.update(_from_annovar(info_map))
            variants.append(v)
    return variants, {"format": "vcf", "annotation": annotation, "assembly": _assembly(header),
                      "tumour_sample": tumour, "normal_sample": normal}


def _tumour_sample(header, columns):
    samples = columns[9:]
    for line in header:
        match = re.match(r"##tumor_sample=(.+)", line)
        if match and match.group(1) in samples:
            return match.group(1)
    for name in samples:
        if re.search(r"tumou?r|primary|-01[A-Z]?$", name, re.IGNORECASE):
            return name
    return samples[0] if samples else None


def _normal_sample(header, columns, tumour):
    samples = [s for s in columns[9:] if s != tumour]
    for line in header:
        match = re.match(r"##normal_sample=(.+)", line)
        if match and match.group(1) in samples:
            return match.group(1)
    return next((s for s in samples if re.search(r"normal|blood|germline|-1\d[A-Z]?$", s, re.IGNORECASE)),
                samples[0] if samples else None)


def _reads(sample, alt_index):
    """(depth, ALT reads) from FORMAT DP / AD; None when the caller didn't write them."""
    try:
        depths = [int(x) for x in sample["AD"].split(",")] if "AD" in sample else None
    except ValueError:
        depths = None
    alt = depths[alt_index + 1] if depths and len(depths) > alt_index + 1 else None
    try:
        depth = int(sample["DP"]) if sample.get("DP") not in (None, ".") else (sum(depths) if depths else None)
    except ValueError:
        depth = sum(depths) if depths else None
    return depth, alt


def _sample_values(columns, row, sample):
    if not sample or "FORMAT" not in columns or len(row) <= columns.index(sample):
        return {}
    keys = row[columns.index("FORMAT")].split(":")
    return dict(zip(keys, row[columns.index(sample)].split(":")))


def _vaf(sample, alt_index):
    try:
        if "AF" in sample:
            return round(float(sample["AF"].split(",")[alt_index]), 3)
        if "AD" in sample:
            depths = [int(x) for x in sample["AD"].split(",")]
            return round(depths[alt_index + 1] / sum(depths), 3) if sum(depths) else None
    except (ValueError, IndexError):
        pass
    return None


def _assembly(header):
    text = "\n".join(header)
    if re.search(r"GRCh38|hg38|\bb38\b", text, re.IGNORECASE):
        return "GRCh38"
    if re.search(r"GRCh37|hg19|\bb37\b|hs37d5|human_g1k_v37", text, re.IGNORECASE):
        return "GRCh37"
    length = re.search(r"##contig=<ID=(?:chr)?1,length=(\d+)", text)
    if length:
        return {GRCH38_CHR1: "GRCh38", GRCH37_CHR1: "GRCh37"}.get(int(length.group(1)))
    return None


def _from_csq(value, fields, alt):
    """VEP CSQ: prefer the canonical transcript of this allele."""
    if not value or not fields:
        return {}
    entries = [dict(zip(fields, e.split("|"))) for e in value.split(",")]
    entries = [e for e in entries if e.get("Allele") in (alt, alt[1:] or "-")] or entries
    entries.sort(key=lambda e: (e.get("CANONICAL") != "YES", not e.get("HGVSp")))
    e = entries[0]
    term, cls = classify(e.get("Consequence", "").split("&"))
    return {"gene": e.get("SYMBOL") or None, "consequence": term, "classification": cls,
            "hgvsp": e.get("HGVSp") or None, "hgvsc": e.get("HGVSc") or None,
            "protein_change": short_protein(e.get("HGVSp")), "exon": _number(e.get("EXON")),
            "intron": _number(e.get("INTRON")), "transcript": e.get("Feature") or None,
            "clinvar": [s for s in (e.get("CLIN_SIG") or "").split("&") if s]}


def _from_ann(value, alt):
    """snpEff ANN: Allele|Annotation|Impact|Gene_Name|Gene_ID|Feature_Type|Feature_ID|Biotype|Rank|HGVS.c|HGVS.p"""
    if not value:
        return {}
    entries = [e.split("|") for e in value.split(",")]
    entries = [e for e in entries if e and e[0] == alt] or entries
    e = entries[0] + [""] * 11
    term, cls = classify(e[1].split("&"))
    return {"gene": e[3] or None, "consequence": term, "classification": cls, "hgvsp": e[10] or None,
            "hgvsc": e[9] or None, "protein_change": short_protein(e[10]),
            "exon": _number(e[8]) if "exon" in e[1] or e[10] else None, "transcript": e[6] or None, "clinvar": []}


def _from_annovar(info):
    """ANNOVAR: AAChange.refGene=EGFR:NM_005228:exon21:c.T2573G:p.L858R,..."""
    gene_key = next((k for k in info if k.startswith("Gene.")), None)
    change_key = next((k for k in info if k.startswith("AAChange.")), None)
    func_key = next((k for k in info if k.startswith("ExonicFunc.")), None)
    region_key = next((k for k in info if k.startswith("Func.")), None)
    first = (str(info.get(change_key) or "").split(",")[0]).split(":")
    exonic = str(info.get(func_key) or "").replace("\\x3b", ";")
    region = str(info.get(region_key) or "")
    cls = {"nonsynonymous_SNV": "missense", "stopgain": "nonsense", "stoploss": "stop_lost",
           "frameshift_deletion": "frameshift", "frameshift_insertion": "frameshift",
           "nonframeshift_deletion": "inframe_deletion", "nonframeshift_insertion": "inframe_insertion",
           "synonymous_SNV": "synonymous"}.get(exonic.replace(" ", "_"))
    if not cls:
        cls = "splice" if "splicing" in region else "noncoding"
    protein = first[4] if len(first) > 4 else None
    return {"gene": (str(info.get(gene_key) or "").split(";")[0]) or None, "consequence": exonic or region or None,
            "classification": cls, "hgvsp": protein, "hgvsc": first[3] if len(first) > 3 else None,
            "protein_change": short_protein(protein), "exon": _number(first[2][4:]) if len(first) > 2 else None,
            "transcript": first[1] if len(first) > 1 else None, "clinvar": []}


# ---------- MAF ----------

def parse_maf(text):
    """A MAF (GDC, cBioPortal data_mutations.txt) for one tumour sample."""
    lines = [line for line in text.splitlines() if line.strip() and not line.startswith("#")]
    if not lines or "Hugo_Symbol" not in lines[0]:
        raise APIError("This is not a MAF file (the header must include Hugo_Symbol)")
    rows = list(csv.DictReader(io.StringIO("\n".join(lines)), delimiter="\t"))
    samples = sorted({r.get("Tumor_Sample_Barcode") for r in rows if r.get("Tumor_Sample_Barcode")})
    if len(samples) > 1:
        shown = ", ".join(samples[:5]) + (f" and {len(samples) - 5} more" if len(samples) > 5 else "")
        raise APIError(f"The MAF has {len(samples)} tumour samples ({shown}). Upload one sample at a time.")
    builds = {r.get("NCBI_Build") for r in rows if r.get("NCBI_Build")}
    variants = [maf_variant(r) for r in rows]
    assembly = "GRCh38" if builds & {"GRCh38", "38", "hg38"} else "GRCh37" if builds & {"GRCh37", "37", "hg19"} else None
    return variants, {"format": "maf", "annotation": "MAF", "assembly": assembly,
                      "tumour_sample": samples[0] if samples else None}


def maf_variant(r):
    alt = r.get("Tumor_Seq_Allele2") or r.get("Tumor_Seq_Allele1") or ""
    flt = r.get("FILTER") or "PASS"
    def count(key):
        try:
            return int(r[key]) if r.get(key) not in (None, "", ".") else None
        except ValueError:
            return None

    t_alt, t_ref, t_depth = count("t_alt_count"), count("t_ref_count"), count("t_depth")
    t_depth = t_depth or ((t_alt or 0) + (t_ref or 0) or None)
    vaf = round(t_alt / t_depth, 3) if t_alt is not None and t_depth else None
    consequence = r.get("Consequence") or r.get("Variant_Classification")
    cls = MAF_CLASSES.get(r.get("Variant_Classification"), "noncoding")
    return {
        "chrom": str(r.get("Chromosome") or "").removeprefix("chr"), "pos": int(r.get("Start_Position") or 0),
        "ref": r.get("Reference_Allele"), "alt": alt, "filter": "PASS" if flt in (".", "PASS") else flt,
        "gene": r.get("Hugo_Symbol") or None, "consequence": consequence, "classification": cls,
        "hgvsp": r.get("HGVSp") or r.get("HGVSp_Short") or None, "hgvsc": r.get("HGVSc") or None,
        "protein_change": short_protein(r.get("HGVSp_Short") or r.get("HGVSp")),
        "exon": _number(r.get("Exon_Number")), "transcript": r.get("Transcript_ID") or None, "vaf": vaf,
        "depth": t_depth, "alt_reads": t_alt, "normal_depth": count("n_depth"), "normal_alt_reads": count("n_alt_count"),
        "clinvar": [s for s in (r.get("CLIN_SIG") or "").split(",") if s and s != "."],
    }


def parse_upload(text, filename=""):
    """Pick the parser from the content (a VCF starts with ##fileformat=VCF)."""
    if text.lstrip().startswith("##fileformat=VCF"):
        return parse_vcf(text)
    if "Hugo_Symbol" in text[:20000]:
        return parse_maf(text)
    raise APIError(f"Could not read {filename or 'the file'}: upload a VCF (##fileformat=VCF) or a MAF "
                   "(tab-separated with a Hugo_Symbol column)")
