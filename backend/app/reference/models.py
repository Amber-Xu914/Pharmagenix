"""Reference tables. Each import replaces a source's rows and records a SourceRelease."""

from ..extensions import db
from ..models import LongText, iso, utcnow


class SourceRelease(db.Model):
    """One row per import run, so every piece of local evidence can be traced to a data version."""

    __tablename__ = "source_releases"

    id = db.Column(db.Integer, primary_key=True)
    source = db.Column(db.String(32), nullable=False, index=True)  # hgnc | chembl | cpic | clinpgx | dgidb | fda | pbs
    version = db.Column(db.String(100))
    url = db.Column(db.String(500))
    record_counts = db.Column(db.JSON)
    status = db.Column(db.String(16), nullable=False, default="ok")  # ok | failed
    notes = db.Column(db.Text)
    imported_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    def to_dict(self):
        return {
            "source": self.source,
            "version": self.version,
            "url": self.url,
            "record_counts": self.record_counts,
            "status": self.status,
            "notes": self.notes,
            "imported_at": iso(self.imported_at),
        }


# ---------- HGNC ----------

class Gene(db.Model):
    __tablename__ = "genes"

    hgnc_id = db.Column(db.String(20), primary_key=True)  # e.g. HGNC:11998
    symbol = db.Column(db.String(64), nullable=False, index=True)
    name = db.Column(db.String(255))
    locus_group = db.Column(db.String(64))
    locus_type = db.Column(db.String(64))
    location = db.Column(db.String(64))
    entrez_id = db.Column(db.String(20), index=True)
    ensembl_gene_id = db.Column(db.String(32), index=True)
    uniprot_ids = db.Column(db.JSON)


class GeneAlias(db.Model):
    """Every name that should resolve to a gene: approved symbol, previous symbols and aliases."""

    __tablename__ = "gene_aliases"

    id = db.Column(db.Integer, primary_key=True)
    hgnc_id = db.Column(db.String(20), db.ForeignKey("genes.hgnc_id", ondelete="CASCADE"), nullable=False,
                        index=True)
    alias = db.Column(db.String(64), nullable=False)
    alias_norm = db.Column(db.String(64), nullable=False, index=True)
    alias_type = db.Column(db.String(16), nullable=False)  # symbol | previous | alias


# ---------- ChEMBL ----------

class Drug(db.Model):
    __tablename__ = "drugs"

    chembl_id = db.Column(db.String(20), primary_key=True)  # parent molecule, e.g. CHEMBL1771
    pref_name = db.Column(db.String(255), index=True)
    molecule_type = db.Column(db.String(64))
    max_phase = db.Column(db.Float)  # 4 = approved
    first_approval = db.Column(db.Integer)


class DrugSynonym(db.Model):
    __tablename__ = "drug_synonyms"

    id = db.Column(db.Integer, primary_key=True)
    chembl_id = db.Column(db.String(20), db.ForeignKey("drugs.chembl_id", ondelete="CASCADE"), nullable=False,
                          index=True)
    synonym = db.Column(db.String(255), nullable=False)
    synonym_norm = db.Column(db.String(255), nullable=False, index=True)
    syn_type = db.Column(db.String(32), nullable=False)  # PREF_NAME | INN | TRADE_NAME | USAN | ...


class DrugMechanism(db.Model):
    """ChEMBL curated mechanism of action, one row per (mechanism, human gene)."""

    __tablename__ = "drug_mechanisms"

    id = db.Column(db.Integer, primary_key=True)
    mec_id = db.Column(db.Integer, nullable=False, index=True)  # ChEMBL record; repeats for multi-gene targets
    chembl_id = db.Column(db.String(20), nullable=False, index=True)
    drug_name = db.Column(db.String(255))
    target_chembl_id = db.Column(db.String(20), nullable=False)
    target_name = db.Column(db.String(255))
    target_type = db.Column(db.String(64))
    hgnc_id = db.Column(db.String(20), index=True)
    gene_symbol = db.Column(db.String(64), nullable=False, index=True)
    action_type = db.Column(db.String(64))
    mechanism_of_action = db.Column(db.String(500))
    direct_interaction = db.Column(db.Boolean)
    disease_efficacy = db.Column(db.Boolean)
    max_phase = db.Column(db.Float)
    pmids = db.Column(db.JSON)
    ref_urls = db.Column(db.JSON)


# ---------- CPIC ----------

class CpicPair(db.Model):
    __tablename__ = "cpic_pairs"

    pair_id = db.Column(db.Integer, primary_key=True)
    gene_symbol = db.Column(db.String(64), nullable=False, index=True)
    hgnc_id = db.Column(db.String(20), index=True)
    drug_rxnorm_id = db.Column(db.String(32))
    drug_name = db.Column(db.String(255), nullable=False)
    drug_name_norm = db.Column(db.String(255), nullable=False, index=True)
    chembl_id = db.Column(db.String(20), index=True)
    guideline_id = db.Column(db.Integer, index=True)
    guideline_name = db.Column(db.String(255))
    guideline_url = db.Column(db.String(500))
    cpic_level = db.Column(db.String(8))  # A, A/B, B, B/C, C, C/D, D
    clinpgx_level = db.Column(db.String(8))
    pgx_testing = db.Column(db.String(64))
    used_for_recommendation = db.Column(db.Boolean)
    pmids = db.Column(db.JSON)


class CpicRecommendation(db.Model):
    __tablename__ = "cpic_recommendations"

    id = db.Column(db.Integer, primary_key=True)
    guideline_id = db.Column(db.Integer, index=True)
    drug_name = db.Column(db.String(255))
    drug_name_norm = db.Column(db.String(255), index=True)
    population = db.Column(db.String(128))
    lookup = db.Column(db.JSON)  # {"CYP2C19": "Poor Metabolizer"} etc.
    implications = db.Column(db.JSON)
    recommendation = db.Column(LongText)
    classification = db.Column(db.String(32))  # Strong | Moderate | Optional | No recommendation
    comments = db.Column(db.Text)


class CpicAllele(db.Model):
    """CPIC allele function assignments (e.g. CYP2C19*2: No function, activity value 0)."""

    __tablename__ = "cpic_alleles"

    id = db.Column(db.Integer, primary_key=True)
    gene_symbol = db.Column(db.String(64), nullable=False, index=True)
    allele_name = db.Column(db.String(128), nullable=False)
    function_status = db.Column(db.String(128))
    clinical_function_status = db.Column(db.String(128))
    clinical_function_substrate = db.Column(db.String(128))
    activity_value = db.Column(db.String(16))
    strength = db.Column(db.String(32))  # evidence strength for the function assignment
    pmids = db.Column(db.JSON)


class CpicAlleleFrequency(db.Model):
    """Allele frequencies averaged per CPIC biogeographical group."""

    __tablename__ = "cpic_allele_frequencies"

    id = db.Column(db.Integer, primary_key=True)
    gene_symbol = db.Column(db.String(64), nullable=False, index=True)
    allele_name = db.Column(db.String(128), nullable=False)
    population_group = db.Column(db.String(64), nullable=False)
    subject_count = db.Column(db.Integer)
    freq_weighted_avg = db.Column(db.Float)
    freq_avg = db.Column(db.Float)
    freq_min = db.Column(db.Float)
    freq_max = db.Column(db.Float)


class CpicGeneResult(db.Model):
    """CPIC phenotype (gene result) with its example CDS consultation text and frequency per group."""

    __tablename__ = "cpic_gene_results"

    id = db.Column(db.Integer, primary_key=True)
    gene_symbol = db.Column(db.String(64), nullable=False, index=True)
    result = db.Column(db.String(128), nullable=False)  # e.g. Poor Metabolizer
    activity_score = db.Column(db.String(16))
    ehr_priority = db.Column(db.String(64))
    consultation_text = db.Column(db.Text)
    frequency = db.Column(db.JSON)  # {"European": 0.024, ...}


class CpicDiplotype(db.Model):
    """CPIC diplotype → phenotype mapping (e.g. CYP2C19 *2/*2 → Poor Metabolizer)."""

    __tablename__ = "cpic_diplotypes"

    id = db.Column(db.Integer, primary_key=True)
    gene_symbol = db.Column(db.String(64), nullable=False, index=True)
    diplotype = db.Column(db.String(160), nullable=False)
    function1 = db.Column(db.String(128))
    function2 = db.Column(db.String(128))
    activity_value1 = db.Column(db.String(16))
    activity_value2 = db.Column(db.String(16))
    total_activity_score = db.Column(db.String(16))
    phenotype = db.Column(db.String(128))
    lookup_key = db.Column(db.JSON)  # the key recommendations and test alerts are looked up by


class CpicTestAlert(db.Model):
    """CPIC example clinical decision support alerts, pre-test and post-test."""

    __tablename__ = "cpic_test_alerts"

    id = db.Column(db.Integer, primary_key=True)
    drug_rxnorm_id = db.Column(db.String(32))
    drug_name = db.Column(db.String(255))
    drug_name_norm = db.Column(db.String(255), index=True)
    cds_context = db.Column(db.String(16))  # Pre-test | Post-test
    population = db.Column(db.String(128))
    genes = db.Column(db.JSON)
    lookup_key = db.Column(db.JSON)
    alert_text = db.Column(db.JSON)  # list of paragraphs


# ---------- ClinPGx (PharmGKB) ----------
# Multi-gene / multi-drug records are stored one row per (record, gene, drug) so they can be filtered by an
# indexed column; the record ID repeats, as with ChEMBL mechanisms.

class ClinpgxRelationship(db.Model):
    """ClinPGx gene–chemical relationships (associated / not associated / ambiguous) with their evidence types."""

    __tablename__ = "clinpgx_relationships"

    id = db.Column(db.Integer, primary_key=True)
    gene_pa_id = db.Column(db.String(20), nullable=False)
    gene_symbol = db.Column(db.String(64), nullable=False, index=True)
    hgnc_id = db.Column(db.String(20))
    chemical_pa_id = db.Column(db.String(20), nullable=False)
    drug_name = db.Column(db.String(255), nullable=False)
    drug_name_norm = db.Column(db.String(255), nullable=False, index=True)
    chembl_id = db.Column(db.String(20), index=True)
    evidence = db.Column(db.JSON)  # ["VariantAnnotation", "LabelAnnotation", ...]
    association = db.Column(db.String(32))
    pk = db.Column(db.Boolean)
    pd = db.Column(db.Boolean)
    pmids = db.Column(db.JSON)


class ClinpgxDrugLabel(db.Model):
    """Pharmacogenomic annotations of drug labels from FDA, EMA, PMDA, HCSC and Swissmedic."""

    __tablename__ = "clinpgx_drug_labels"

    id = db.Column(db.Integer, primary_key=True)
    label_id = db.Column(db.String(20), nullable=False, index=True)
    name = db.Column(db.String(500))
    source = db.Column(db.String(32))  # FDA | EMA | PMDA | HCSC | Swissmedic
    gene_symbol = db.Column(db.String(64), index=True)
    hgnc_id = db.Column(db.String(20))
    drug_name = db.Column(db.String(255))
    drug_name_norm = db.Column(db.String(255), index=True)
    chembl_id = db.Column(db.String(20), index=True)
    biomarker_flag = db.Column(db.String(32))
    testing_level = db.Column(db.String(64))  # Testing Required | Testing Recommended | Actionable PGx | ...
    has_prescribing_info = db.Column(db.Boolean)
    has_dosing_info = db.Column(db.Boolean)
    has_alternate_drug = db.Column(db.Boolean)
    has_other_prescribing_guidance = db.Column(db.Boolean)
    cancer_genome = db.Column(db.Boolean)
    variants = db.Column(db.Text)
    latest_history_date = db.Column(db.Date)
    url = db.Column(db.String(255))


class ClinpgxGuideline(db.Model):
    """Guideline annotations from CPIC, DPWG, CPNDS, RNPGx and others (one row per guideline)."""

    __tablename__ = "clinpgx_guidelines"

    guideline_id = db.Column(db.String(20), primary_key=True)  # e.g. PA166104939
    name = db.Column(db.String(500), nullable=False)
    source = db.Column(db.String(32), index=True)  # CPIC | DPWG | CPNDS | RNPGx | ...
    recommendation = db.Column(db.Boolean)
    dosing_information = db.Column(db.Boolean)
    alternate_drug_available = db.Column(db.Boolean)
    other_prescribing_guidance = db.Column(db.Boolean)
    has_testing_info = db.Column(db.Boolean)
    pediatric = db.Column(db.Boolean)
    cancer_genome = db.Column(db.Boolean)
    summary = db.Column(db.Text)
    text = db.Column(LongText)
    pmids = db.Column(db.JSON)
    url = db.Column(db.String(255))


class ClinpgxGuidelinePair(db.Model):
    __tablename__ = "clinpgx_guideline_pairs"

    id = db.Column(db.Integer, primary_key=True)
    guideline_id = db.Column(db.String(20), db.ForeignKey("clinpgx_guidelines.guideline_id", ondelete="CASCADE"),
                             nullable=False, index=True)
    gene_symbol = db.Column(db.String(64), index=True)
    hgnc_id = db.Column(db.String(20))
    drug_name = db.Column(db.String(255))
    drug_name_norm = db.Column(db.String(255), index=True)
    chembl_id = db.Column(db.String(20), index=True)


# ---------- DGIdb ----------
# Interactions are queried live from the DGIdb API on every search; only the gene categories are kept locally.

class DgidbGeneCategory(db.Model):
    """Druggable-genome categories (e.g. KINASE, CLINICALLY ACTIONABLE) per gene and source."""

    __tablename__ = "dgidb_gene_categories"

    id = db.Column(db.Integer, primary_key=True)
    gene_symbol = db.Column(db.String(64), nullable=False, index=True)
    hgnc_id = db.Column(db.String(20))
    gene_claim_name = db.Column(db.String(255))
    category = db.Column(db.String(128), nullable=False)
    source_db = db.Column(db.String(64))


# ---------- FDA Table of Pharmacogenomic Biomarkers in Drug Labeling ----------

class FdaPgxBiomarker(db.Model):
    """One row per (table row, gene). Non-gene biomarkers (e.g. "Chromosome 5q", "Tumor Mutational Burden")
    and genes not in HGNC keep one row with gene_symbol NULL."""

    __tablename__ = "fda_pgx_biomarkers"

    id = db.Column(db.Integer, primary_key=True)
    row_number = db.Column(db.Integer, nullable=False)  # position in the FDA table
    drug_label = db.Column(db.String(255), nullable=False)  # as in the table, e.g. "Abemaciclib (2)"
    drug_name = db.Column(db.String(255), nullable=False)
    drug_name_norm = db.Column(db.String(255), nullable=False, index=True)
    chembl_id = db.Column(db.String(20), index=True)
    therapeutic_area = db.Column(db.String(128))
    biomarker = db.Column(db.String(255), nullable=False)  # as in the table, e.g. "ERBB2 (HER2)"
    gene_symbol = db.Column(db.String(64), index=True)
    hgnc_id = db.Column(db.String(20))
    labeling_sections = db.Column(db.JSON)


# ---------- PBS (Australia) ----------

class PbsItem(db.Model):
    """Current PBS Schedule item; one row per PBS code and brand."""

    __tablename__ = "pbs_items"

    id = db.Column(db.Integer, primary_key=True)
    pbs_code = db.Column(db.String(16), nullable=False, index=True)
    drug_name = db.Column(db.String(255), nullable=False)
    drug_name_norm = db.Column(db.String(255), nullable=False, index=True)
    chembl_id = db.Column(db.String(20), index=True)
    brand_name = db.Column(db.String(255))
    brand_name_norm = db.Column(db.String(255), index=True)
    form = db.Column(db.String(255))
    schedule_form = db.Column(db.String(500))
    program_code = db.Column(db.String(8))
    benefit_type_code = db.Column(db.String(4))  # U unrestricted | R restricted | A authority | S streamlined
    route = db.Column(db.String(64))
    schedule_code = db.Column(db.Integer)
    effective_date = db.Column(db.Date)
