import os

from dotenv import load_dotenv

load_dotenv()


def _float_or_none(value):
    return float(value) if value not in (None, "") else None


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")
    ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "change-me-admin-token")
    CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*")

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

    # Local reference datasets (imported with `flask --app run import-data`)
    HGNC_URL = os.getenv(
        "HGNC_URL", "https://storage.googleapis.com/public-download-files/hgnc/tsv/tsv/hgnc_complete_set.txt")
    CHEMBL_API_URL = os.getenv("CHEMBL_API_URL", "https://www.ebi.ac.uk/chembl/api/data")
    CPIC_API_URL = os.getenv("CPIC_API_URL", "https://api.cpicpgx.org/v1")
    PBS_API_URL = os.getenv("PBS_API_URL", "https://data-api.health.gov.au/pbs/api/v3")
    # Published key for unregistered public users; shared rate limit of 1 request per 20 seconds.
    PBS_SUBSCRIPTION_KEY = os.getenv("PBS_SUBSCRIPTION_KEY") or "2384af7c667342ceb5a736fe29f1dc6b"
    PBS_MIN_INTERVAL = float(os.getenv("PBS_MIN_INTERVAL", "21"))

    # Limits
    MAX_ENTITIES = 50              # genes/drugs per query (gene panels included)
    MAX_INTERACTIONS = 300         # rows kept in the table / network
    MAX_PROMPT_EVIDENCE = 80       # evidence items passed to the LLM
    MAX_PUBLICATIONS = 15
    MAX_VARIANT_ANNOTATIONS = 40
    MAX_MECHANISMS = 100
    MAX_GUIDELINES = 30
    MAX_PBS_DRUGS = 20             # drugs checked for Australian PBS listing per query


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {}
    ADMIN_TOKEN = "test-token"
    OPENAI_API_KEY = "test"
    ANTHROPIC_API_KEY = "test"
    PBS_MIN_INTERVAL = 0
