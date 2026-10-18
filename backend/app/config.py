import os
from datetime import timedelta

from dotenv import load_dotenv

load_dotenv()


BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _sqlite_uri(uri):
    """A relative SQLite URI (sqlite:///mlflow.db) is made absolute under backend/, so the app and `flask mlflow-ui`
    use the same database whatever folder they are started from."""
    prefix = "sqlite:///"
    if uri.startswith(prefix) and not uri.startswith(prefix + "/"):
        return prefix + os.path.join(BACKEND_DIR, uri[len(prefix):])
    return uri


def _float_or_none(value):
    return float(value) if value not in (None, "") else None


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")
    ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "change-me-admin-token")
    CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*")

    # User login uses Flask's signed session cookie, so SECRET_KEY must be set to a random value in .env.
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME = timedelta(days=7)

    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL",
        "mysql+pymysql://dgi:dgi_password@localhost:3306/dgi_explorer?charset=utf8mb4",
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True, "pool_recycle": 280}

    # LLM providers. "primary" answers every query; "comparison" runs when the user asks for it.
    PRIMARY_PROVIDER = "openai"
    COMPARISON_PROVIDER = "anthropic"
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-6-luna")
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
    ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
    LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "4000"))
    LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "90"))
    LLM_TEMPERATURE = _float_or_none(os.getenv("LLM_TEMPERATURE"))

    # Biomedical sources
    DGIDB_URL = os.getenv("DGIDB_URL", "https://dgidb.org/api/graphql")
    PUBTATOR_URL = os.getenv("PUBTATOR_URL", "https://www.ncbi.nlm.nih.gov/research/pubtator3-api")
    PHARMGKB_URL = os.getenv("PHARMGKB_URL", "https://api.clinpgx.org/v1")
    DRUGBANK_URL = os.getenv("DRUGBANK_URL", "https://api.drugbank.com/v1")
    DRUGBANK_API_KEY = os.getenv("DRUGBANK_API_KEY", "")
    HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "20"))
    # Gene profile (workflow stage 2)
    UNIPROT_URL = os.getenv("UNIPROT_URL", "https://rest.uniprot.org")
    # Workflow 4 (patient VCF): variant annotation and OncoKB (optional; needs a licensed token)
    VEP_URL = os.getenv("VEP_URL", "https://rest.ensembl.org")                  # GRCh38
    VEP_GRCH37_URL = os.getenv("VEP_GRCH37_URL", "https://grch37.rest.ensembl.org")
    ONCOKB_URL = os.getenv("ONCOKB_URL", "https://www.oncokb.org/api/v1")
    ONCOKB_API_TOKEN = os.getenv("ONCOKB_API_TOKEN", "")

    # Local reference datasets (imported with `flask --app run import-data`)
    HGNC_URL = os.getenv(
        "HGNC_URL", "https://storage.googleapis.com/public-download-files/hgnc/tsv/tsv/hgnc_complete_set.txt")
    CHEMBL_API_URL = os.getenv("CHEMBL_API_URL", "https://www.ebi.ac.uk/chembl/api/data")
    CPIC_API_URL = os.getenv("CPIC_API_URL", "https://api.cpicpgx.org/v1")
    CLINPGX_DOWNLOAD_URL = os.getenv("CLINPGX_DOWNLOAD_URL", "https://api.clinpgx.org/v1/download/file/data")
    DGIDB_DOWNLOAD_URL = os.getenv("DGIDB_DOWNLOAD_URL", "https://dgidb.org/data/latest")
    FDA_BIOMARKERS_URL = os.getenv(
        "FDA_BIOMARKERS_URL",
        "https://www.fda.gov/drugs/science-and-research-drugs/table-pharmacogenomic-biomarkers-drug-labeling")
    # Workflow 4 (patient VCF): cancer types, driver genes and variant-level drug evidence
    ONCOTREE_API_URL = os.getenv("ONCOTREE_API_URL", "https://oncotree.info/api")
    INTOGEN_DRIVERS_URL = os.getenv(
        "INTOGEN_DRIVERS_URL", "https://www.intogen.org/download?file=IntOGen-Drivers-20240920.zip")
    CIVIC_EVIDENCE_URL = os.getenv(
        "CIVIC_EVIDENCE_URL", "https://civicdb.org/downloads/nightly/nightly-ClinicalEvidenceSummaries.tsv")
    CBIOPORTAL_URL = os.getenv("CBIOPORTAL_URL", "https://www.cbioportal.org/api")
    CBIOPORTAL_SUBTYPE_STUDIES = {  # OncoTree code -> reference study for gene frequencies (Tier 3 R)
        "LUAD": "luad_tcga_pan_can_atlas_2018", "LUSC": "lusc_tcga_pan_can_atlas_2018",
        "NSCLC": "nsclc_tcga_broad_2016", "SCLC": "sclc_ucologne_2015",
    }
    CANCER_HOTSPOTS_URL = os.getenv("CANCER_HOTSPOTS_URL", "https://www.cancerhotspots.org/api/hotspots/single")
    PBS_API_URL = os.getenv("PBS_API_URL", "https://data-api.health.gov.au/pbs/api/v3")
    # Published key for unregistered public users; shared rate limit of 1 request per 20 seconds.
    PBS_SUBSCRIPTION_KEY = os.getenv("PBS_SUBSCRIPTION_KEY") or "2384af7c667342ceb5a736fe29f1dc6b"
    PBS_MIN_INTERVAL = float(os.getenv("PBS_MIN_INTERVAL", "21"))

    # MLflow (optional): set MLFLOW_TRACKING_URI (e.g. sqlite:///mlflow.db) to track every search as a run + trace
    MLFLOW_TRACKING_URI = _sqlite_uri(os.getenv("MLFLOW_TRACKING_URI", ""))
    MLFLOW_EXPERIMENT_PREFIX = os.getenv("MLFLOW_EXPERIMENT_PREFIX", "pharmagenix")
    MLFLOW_LOG_ARTIFACTS = os.getenv("MLFLOW_LOG_ARTIFACTS", "true").lower() == "true"

    # Limits
    MAX_INTERACTIONS = 300         # rows kept in the table / network
    MAX_PROMPT_EVIDENCE = 80       # evidence items passed to the LLM
    MAX_PUBLICATIONS = 15
    MAX_VARIANT_ANNOTATIONS = 40
    MAX_MECHANISMS = 100
    MAX_GUIDELINES = 30
    MAX_PBS_DRUGS = 20             # drugs checked for Australian PBS listing per query
    MAX_VCF_BYTES = 20_000_000     # uploaded VCF / MAF
    MAX_VCF_VARIANTS = 5000        # PASS variants annotated per upload (VEP REST: 200 per request)
    # Workflow 4 stage 1, strict QC (hard filter)
    VCF_QC_MIN_DEPTH = 20
    VCF_QC_MIN_ALT_READS = 5
    VCF_QC_MIN_VAF = 0.05
    VCF_QC_MAX_POPULATION_AF = 0.01   # gnomAD: above this the variant is treated as germline
    VCF_QC_MAX_NORMAL_ALT_READS = 1   # more ALT reads than this in the matched normal: germline
    # Workflow 4 stage 3: Tier 3 score S = wF·F + wR·R + wB·B, each component 0–1 (example weights, configurable)
    VCF_TIER3_WEIGHTS = {"F": 0.4, "R": 0.3, "B": 0.3}
    VCF_TIER3_R_SATURATION = 0.25     # subtype mutation frequency that counts as R = 1
    VCF_TIER3_B_PARTS = {"cancer_role": 0.4, "pathway": 0.3, "tsg_loss": 0.3}
    VCF_TIER3_MIN_SCORE = 0.4         # below: "low priority" list (kept and reported, not deleted)
    # Workflow 4 stages 6–7: the literature search behind the Literature agent, and the report
    VCF_LITERATURE_QUERIES = 8        # PubTator searches for Tier 3 variants and level 3–4 pairs (0 = off)
    VCF_TOP_PER_TRACK = 10            # drugs shown per track; the full lists go to the appendix
    VCF_AGENTS = True                 # run the exploration agents (LLM) on the exploratory track


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {}
    ADMIN_TOKEN = "test-token"
    OPENAI_API_KEY = "test"
    ANTHROPIC_API_KEY = "test"
    PBS_MIN_INTERVAL = 0
    MLFLOW_TRACKING_URI = ""  # tests that need MLflow turn it on themselves
