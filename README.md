# Pharmagenix — a GenAI-powered drug–gene interaction explorer

Enter a gene or drug (or upload a gene panel), pick the relationship types you care about, and the app combines
curated reference datasets held in MySQL (HGNC, ChEMBL, CPIC and the Australian PBS Schedule) with live evidence from
DGIdb, PharmGKB (ClinPGx), PubTator3 and DrugBank. An LLM then writes a narrative report in which every claim
cites its evidence. Results are shown three ways (a text summary, a structured DGI table and a relationship network), plus the
drugs' Australian PBS listing status.
Users can rate answers, leave suggestions and ask follow-up questions. An admin console shows prompt logs, lets
admins edit prompt templates and lists failed responses.

| Layer | Technology |
|---|---|
| Frontend | Plain JavaScript (ES modules, no build step), Cytoscape.js for the network, marked + DOMPurify for Markdown |
| Backend | Flask 3 + Flask-SQLAlchemy |
| Database | MySQL 8 (developed on 8.4; uses JSON columns) |
| LLM | Primary: OpenAI (`OPENAI_MODEL`, default `gpt-6-luna`). Comparison: Claude Haiku 4.5 |

## Quick start

```bash
# 1. MySQL: create the database and an app user that can only access it.
#    Homebrew's MySQL has no root password by default; add -p if you have set one.
#    On macOS with Anaconda installed, `mysql` may resolve to Anaconda's old 5.7 client, which cannot log in
#    to MySQL 8.4, so call the Homebrew client explicitly.
/opt/homebrew/bin/mysql -u root -e "
  CREATE DATABASE dgi_explorer CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
  CREATE USER 'dgi'@'localhost' IDENTIFIED BY 'choose-a-password';
  GRANT ALL PRIVILEGES ON dgi_explorer.* TO 'dgi'@'localhost';"

# 2. Backend
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # fill in DATABASE_URL (with the password above), API keys, ADMIN_TOKEN
flask --app run init-db       # create tables and seed the default prompt template
flask --app run import-data   # load HGNC, ChEMBL, CPIC and PBS (about 3 minutes; see below)
flask --app run run --debug --host 127.0.0.1 --port 5001  # http://127.0.0.1:5001
```

The explorer is at <http://localhost:5000> and the admin console at <http://localhost:5000/admin> (log in with the
`ADMIN_TOKEN` from `.env`). Flask serves the frontend directly. To host the frontend separately, set
`window.DGI_API_BASE = "http://<backend-host>"` in the page and configure `CORS_ORIGINS`.

Run the tests (in-memory SQLite with fake APIs and LLMs, so no network access or API keys needed):

```bash
cd backend && .venv/bin/python -m pytest -q
```

## Reference datasets (local MySQL)

Live APIs are still queried on every request. The local datasets add what the APIs cannot do quickly or at all: name
normalisation, curated action types, prescribing guidelines and Australian context. Each import replaces that
source's tables in one transaction and records the data version in `source_releases`, and every evidence item from a
local source carries that version.

| Dataset | Used for | Imported | Rows (Sept 2026) | Licence |
|---|---|---|---|---|
| [HGNC](https://www.genenames.org/download/) | Normalising gene input: symbols, previous symbols, aliases, Ensembl and NCBI Gene IDs (`P53 → TP53`) | Full complete-set TSV | ~45k genes, ~106k names | CC0 |
| [ChEMBL](https://www.ebi.ac.uk/chembl/) | Curated mechanisms of action with action types (inhibitor, agonist…), which most DGIdb interactions lack; drug synonyms and trade names (`Plavix → CLOPIDOGREL`) | Mechanism table plus drug names (including salt forms' trade names), via the ChEMBL API | ~5.6k drugs, ~58k names, ~12k mechanism–gene rows | CC BY-SA 3.0 (attribution required) |
| [CPIC](https://cpicpgx.org/) | Genotype/phenotype-based prescribing recommendations for the SNP-specific relationship type | Pairs and recommendations, via the CPIC API | ~630 pairs, ~2.1k recommendations | See cpicpgx.org |
| [PBS Schedule](https://www.pbs.gov.au/) | Whether a drug is PBS-listed in Australia, its item codes, forms and benefit type (shown separately; not DGI evidence and not sent to the LLM) | Current month's schedule, via the PBS public API | ~15k item/brand rows | © Commonwealth of Australia; redistribution permitted, modification not |

```bash
flask --app run import-data                 # all four, in dependency order (HGNC → ChEMBL → CPIC → PBS)
flask --app run import-data pbs             # just one; PBS updates on the 1st of each month
flask --app run reference-status            # what is loaded, and which versions
```

The app works without the reference data (sources show as "not enabled" and input is passed to the APIs unchanged),
but results are noticeably better with it.

Datasets considered but not imported yet:

- **Full ChEMBL database dump** (~35 GB in MySQL): the activity data (IC50, Ki…) is out of scope for now, and DGIdb
  already aggregates ChEMBL's drug–target interactions. The curated mechanism table is the part that adds value.
- **ClinVar**: variant–disease significance, not drug response, and millions of rows. Worth adding later as a
  "variant context" layer, filtered to the genes users actually query.
- **Open Targets, CIViC**: CIViC is already one of DGIdb's sources. Open Targets fits a later target–disease view.
- **TGA ARTG**: would answer "can this be supplied in Australia" beyond PBS subsidy.

## Data flow

```
User input ──► POST /api/explore
                │
                ├─ reference/normalise.py       P53 → TP53 (HGNC), Plavix → CLOPIDOGREL (ChEMBL, PBS brands)
                │
                ├─ services/biomed/aggregator.py   sources are called in turn, each in its own try/except,
                │                                  so one failing source doesn't sink the query
                │    ├─ DGIdb (GraphQL)       drug–gene interactions, types, scores, PMIDs
                │    ├─ ChEMBL (local)        curated mechanisms of action with action types
                │    ├─ CPIC (local)          prescribing recommendations (only when SNP is selected)
                │    ├─ PharmGKB/ClinPGx      variant (SNP/haplotype)–drug clinical annotations, evidence
                │    │                        levels 1A–4 (only requested when SNP is selected)
                │    ├─ DrugBank              mechanism of action (needs a licensed key; skipped otherwise)
                │    ├─ PubTator3             related literature
                │    └─ PBS (local)           Australian listing for the query drugs and related approved drugs
                │    ⇒ evidence = { normalisation, items (E1..En), table, network, australian_availability,
                │                   source_status }, stored in queries.evidence
                │
                ├─ services/llm/report_generator.py
                │    ├─ renders the prompt from the active template (sandboxed Jinja2), evidence numbered
                │    ├─ calls the primary and comparison models in parallel
                │    ├─ checks citations: unknown IDs / no citations / truncated output → status=flagged
                │    └─ writes a prompt_logs row for every call, failures included
                ▼
             returns query + reports + evidence → frontend renders summary / table / network / PBS panel
```

The table and the network are built straight from the API data, never by the LLM, so the model cannot invent entries
there. The LLM only writes the narrative, and every sentence must cite an evidence ID like `[E3]`.

## Project layout

```
backend/
  run.py                      Flask entry point
  schema.sql                  MySQL DDL generated from the models (reference only; init-db creates the tables)
  app/
    __init__.py               create_app: registers blueprints, serves the frontend static files
    config.py                 all settings (read from .env)
    models.py                 queries / reports / prompt_logs / prompt_templates / feedback
    cli.py                    flask init-db / import-data / reference-status / dump-schema
    routes/
      explore.py              /api/meta, /api/explore, /api/queries/<id>[/ask], /api/panels/parse
      feedback.py             /api/reports/<id>/feedback
      admin.py                /api/admin/* (X-Admin-Token auth)
    reference/                local datasets
      models.py               genes, gene_aliases, drugs, drug_synonyms, drug_mechanisms, cpic_*, pbs_items,
                              source_releases
      importers/              hgnc.py, chembl.py, cpic.py, pbs.py (one per source)
      normalise.py            input → standard gene / drug identifiers
      lookup.py               local evidence queries
      text.py                 name normalisation (case, whitespace, salt forms)
    services/
      explorer.py             orchestrates one exploration: validate → collect evidence → generate reports
      relations.py            relationship types and the DGIdb label mapping
      panel.py                gene panel file parsing
      prompts.py              default template, evidence formatting, template rendering
      biomed/                 dgidb.py, pharmgkb.py, pubtator.py, drugbank.py, aggregator.py
      llm/                    openai_client.py, anthropic_client.py, report_generator.py
  tests/                      pytest
frontend/
  index.html  admin.html
  css/style.css
  js/ api.js  main.js  summary.js  table.js  network.js  availability.js  admin.js  util.js
```

## API reference

| Method | Path | Description |
|---|---|---|
| GET | `/api/meta` | Relationship types, model info, limits |
| POST | `/api/panels/parse` | Upload a gene panel file (multipart field `file`) → list of genes |
| POST | `/api/explore` | `{entities, entity_type: auto\|gene\|drug, relation_types[], compare}` |
| GET | `/api/queries/<id>` | Fetch the full result of a past query |
| POST | `/api/queries/<id>/ask` | Follow-up / re-ask `{followup_question, compare}`; reuses the evidence when entities and relation types are unchanged |
| POST | `/api/reports/<id>/feedback` | `{rating 1–5, comment, suggestion}` |
| GET | `/api/admin/prompt-logs[/<id>]` | Prompt logs (filter by status / provider) |
| GET/POST | `/api/admin/templates` | List templates / save a new version |
| POST | `/api/admin/templates/<id>/activate` | Switch the active template |
| POST | `/api/admin/templates/preview` | Render a template against a stored query's evidence (no LLM call) |
| GET | `/api/admin/failures` | Failed and flagged reports |
| POST | `/api/admin/reports/<id>/retry` | Regenerate with the current template |
| GET | `/api/admin/feedback`, `/api/admin/stats` | User feedback; per-model success rate, latency and average rating |

## Things to check and known limitations

- **OpenAI model ID**: `.env` defaults to `gpt-6-luna`. Confirm the exact model ID for "GPT-6 Luna" in your OpenAI
  dashboard. Calls go through the Responses API. If it is a reasoning model, `LLM_MAX_TOKENS` also covers reasoning
  tokens, and a budget that is too small returns an empty answer (recorded as failed).
- **PharmGKB is now ClinPGx**: `api.pharmgkb.org` no longer responds, so the default is `https://api.clinpgx.org/v1`.
  Drug-name lookups only match lower case (the code handles this). The API allows about 2 requests per second, so each
  query looks up at most 10 genes/drugs.
- **DrugBank requires a paid licence**: without `DRUGBANK_API_KEY` the source is skipped. The endpoint paths in
  `biomed/drugbank.py` follow the common pattern but are unverified; check them against the docs for the product you
  license once you have a key.
- **About 90% of DGIdb interactions have no type label**, so "Unspecified" is selected by default. Unticking it
  removes most results.
- **PBS public API**: the published key for unregistered users is shared by everyone and limited to one request per
  20 seconds, so the importer only fetches the current month (two requests). Register at
  <https://data-api-portal.health.gov.au> for your own key (`PBS_SUBSCRIPTION_KEY`). PBS matching is by generic name
  or ChEMBL ID, so combination products may not be matched, and restriction wording is not imported (the panel links
  to each item on pbs.gov.au).
- Requests are synchronous (20–60 s each). Under real load, move `run_exploration` into a Celery/RQ task and have the
  frontend poll.
- The admin console uses a single shared token. Replace it with real accounts before deploying publicly.
- Schema changes currently rely on `init-db` (`db.create_all` does not alter existing tables). Add Flask-Migrate once
  the models settle.
