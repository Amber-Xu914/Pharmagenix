"""Reference tables. Each import replaces a source's rows and records a SourceRelease."""

from ..extensions import db
from ..models import LongText, iso, utcnow


class SourceRelease(db.Model):
    """One row per import run, so every piece of local evidence can be traced to a data version."""

    __tablename__ = "source_releases"

    id = db.Column(db.Integer, primary_key=True)
    source = db.Column(db.String(32), nullable=False, index=True)  # hgnc | chembl | cpic | pbs
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
