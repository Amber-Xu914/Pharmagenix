# Pharmagenix — a GenAI-powered drug–gene interaction explorer

Enter one gene, pick the relationship types you care about, and the app lists every drug that interacts with it, or
enter a drug (generic or brand name) to list the genes it acts on. Or upload a tumour's somatic variants (VCF, synthetic test data) with its cancer type to get ranked candidate drugs for its
actionable variants (Workflow 4, lung cancer prototype). It combines
curated reference datasets held in MySQL (HGNC, ChEMBL, CPIC, ClinPGx, DGIdb, the FDA biomarker table and the Australian PBS Schedule) with live evidence from
DGIdb, PharmGKB (ClinPGx), PubTator3, UniProt and DrugBank. An LLM then writes a narrative report in which every claim
cites its evidence. Results are shown three ways (a text summary, a structured DGI table and a relationship network), plus the
drugs' Australian PBS listing status.
Users can rate answers, leave suggestions, ask follow-up questions and download any result as a Word report
(abstract, methods, results tables, network figure, the LLM discussion and numbered references), and can create an account and log in with
their email address from the top-right corner of the explorer. An admin console shows prompt logs, lets
admins edit prompt templates and lists failed responses.

| Layer    | Technology                                                                                                  |
| -------- | ----------------------------------------------------------------------------------------------------------- |
| Frontend | Plain JavaScript (ES modules, no build step), Cytoscape.js for the network, marked + DOMPurify for Markdown |
| Backend  | Flask 3 + Flask-SQLAlchemy                                                                                  |
| Database | MySQL 8 (developed on 8.4; uses JSON columns)                                                               |
| LLM      | Primary: Claude Sonnet 4.6 (`ANTHROPIC_MODEL`, temperature 0.3). Comparison: OpenAI `gpt-6-luna`             |

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
flask --app run import-data   # load all reference datasets (about 4 minutes; see below)
flask --app run seed-demo     # sample logins: demo@pharmagenix.test / Demo1234!, admin@pharmagenix.test / Admin1234!
flask --app run run --debug --host 127.0.0.1 --port 5001  # http://127.0.0.1:5001
```

The explorer is at <http://localhost:5001> and the admin console at <http://localhost:5001/admin> (log in as the
admin account, or with the `ADMIN_TOKEN` from `.env`).

- **User manual** (sample logins, every workflow, admin console, demo checklist): [docs/USER_MANUAL.md](docs/USER_MANUAL.md)
- **Database design** (ER diagrams, tables, normalisation, example SQL): [docs/database.md](docs/database.md)

Upgrading a database created by an earlier version: `flask --app run upgrade-db` (adds the new columns and tables,
moves report citations out of JSON; safe to re-run). Before a demo, `flask --app run warm-cache` stores the live API
answers for the example searches, so they still work if an API is down (`HTTP_CACHE`, see `app/http_cache.py`). Flask serves the frontend directly. To host the frontend separately, set
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

| Dataset                                     | Used for                                                                                                                                                         | Imported                                                                                | Rows (Sept 2026)                                  | Licence                                                                 |
| ------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------- | ------------------------------------------------- | ----------------------------------------------------------------------- |
| [HGNC](https://www.genenames.org/download/) | Normalising gene input: symbols, previous symbols, aliases, Ensembl and NCBI Gene IDs (`P53 → TP53`)                                                             | Full complete-set TSV                                                                   | ~45k genes, ~106k names                           | CC0                                                                     |
| [ChEMBL](https://www.ebi.ac.uk/chembl/)     | Curated mechanisms of action with action types (inhibitor, agonist…), which most DGIdb interactions lack; drug synonyms and trade names (`Plavix → CLOPIDOGREL`); drug warnings (withdrawals, boxed warnings) for the safety check | Mechanism table, drug names (including salt forms' trade names) and drug warnings, via the ChEMBL API | ~5.6k drugs, ~58k names, ~12k mechanism–gene rows | CC BY-SA 3.0 (attribution required)                                     |
| [CPIC](https://cpicpgx.org/)                | Genotype/phenotype-based prescribing recommendations for the SNP-specific relationship type; the supporting guideline tables (allele functions and frequencies, diplotype → phenotype mappings, example CDS consultation text, pre-/post-test alerts) for checking reports against the guidelines | Pairs, recommendations and the [cpic-data](https://github.com/cpicpgx/cpic-data) tables, via the CPIC API (cpic-data releases no longer attach database dumps) | ~570 pairs, ~2.1k recommendations, ~1.4k alleles, ~7.2k allele frequencies, ~113k diplotypes, ~2.2k alerts | See cpicpgx.org |
| [ClinPGx](https://www.clinpgx.org/downloads) (PharmGKB) | Guideline annotations from CPIC, DPWG, CPNDS, RNPGx and others; PGx drug-label annotations (FDA, EMA, PMDA, HCSC, Swissmedic); gene–chemical relationships. Clinical annotations stay live (ClinPGx API) | `relationships` (gene–chemical rows only), `drugLabels` and `guidelineAnnotations.json` downloads | 219 guidelines (~480 gene–drug pairs), ~2.2k label rows, ~10k relationships | CC BY-NC-SA 4.0 (attribution, non-commercial) |
| [DGIdb](https://dgidb.org/downloads)        | Druggable-genome gene categories (kinase, clinically actionable…) for gene-level filtering. Interactions stay live (DGIdb API) | `categories.tsv`, mapped to normalised genes through `genes.tsv` | ~32k gene categories | Per contributing source; see dgidb.org |
| [FDA PGx biomarker table](https://www.fda.gov/drugs/science-and-research-drugs/table-pharmacogenomic-biomarkers-drug-labeling) | Which FDA labels name a pharmacogenomic biomarker, the therapeutic area and the label sections | Parsed from the HTML page (no download file exists); biomarkers mapped to HGNC symbols where they name them | ~690 table rows, ~750 gene rows | Public domain (US government) |
| [FDA CYP / transporter interaction table](https://www.fda.gov/CYPandTransporterInteractingDrugs) | Drug–drug interaction checks (strong / moderate / weak CYP inhibitors and inducers, sensitive substrates, transporter roles), and drug–gene evidence for CYP and transporter genes (CYP2D6 → paroxetine, strong inhibitor) | Parsed from the HTML page; CYP3A linked to CYP3A4, transporters to their genes (P-gp → ABCB1) | ~245 substances, ~420 role rows | Public domain (US government) |
| [OncoTree](https://oncotree.info/) | Workflow 4: the standard term for the cancer type / subtype (LUAD → NSCLC → LUNG) | Latest stable tumour types, via the OncoTree API | ~900 types | See oncotree.info |
| [IntOGen](https://www.intogen.org/download) | Workflow 4: driver genes per cancer type, for the "cancer relevance" filter (replaces COSMIC CGC, which needs a licence) | Compendium of cancer genes (2024-06-18 release) | ~4.5k gene–cohort rows, 633 genes | CC0 |
| [CIViC](https://civicdb.org/) | Workflow 4: variant-level drug response / resistance evidence, levels A–E; molecular profiles parsed into matchable parts | Nightly predictive evidence summaries | ~2.8k evidence items | CC0 |
| [cancerhotspots.org](https://www.cancerhotspots.org/) | Workflow 4: Tier 2 known hotspots (replaces COSMIC hotspots, which need a licence) | Single-residue and in-frame indel hotspots, via its API | 1,165 hotspots in 247 genes | See cancerhotspots.org (Chang et al. 2018) |
| [cBioPortal](https://www.cbioportal.org/) | Workflow 4: gene mutation frequency per lung subtype (Tier 3 R component) | Mutated-gene counts for one reference study per subtype (`CBIOPORTAL_SUBTYPE_STUDIES`) | ~63k gene–subtype rows (LUAD, LUSC, NSCLC, SCLC) | TCGA / study licences; see cbioportal.org |
| [PBS Schedule](https://www.pbs.gov.au/)     | Whether a drug is PBS-listed in Australia, its item codes, forms, benefit type and restriction text (shown separately; not DGI evidence and not sent to the LLM)                   | Current month's schedule and restrictions, via the PBS public API                                       | ~15k item/brand rows, ~3.6k restrictions          | © Commonwealth of Australia; redistribution permitted, modification not |

```bash
flask --app run import-data                 # all, in dependency order (HGNC → ChEMBL → CPIC → ClinPGx → DGIdb → FDA → FDA DDI → OncoTree → IntOGen → CIViC → hotspots → cBioPortal → PBS)
flask --app run import-data pbs             # just one; PBS updates on the 1st of each month
flask --app run reference-status            # what is loaded, and which versions
```

The app works without the reference data (sources show as "not enabled" and input is passed to the APIs unchanged),
but results are noticeably better with it.

**Live or local.** Evidence that is looked up per gene or drug and needs no joins is queried live on every search,
so it is always current: DGIdb interactions and ClinPGx clinical annotations. Data that is needed for name
normalisation, has no usable API, or is joined across sources (gene → guideline → label → PBS; diplotype →
phenotype → recommendation → alert) is kept locally and versioned: HGNC, ChEMBL, CPIC, the ClinPGx guidelines and
labels, the FDA table and PBS. The new tables (CPIC's supporting tables, ClinPGx, DGIdb categories, FDA) are not read
by the evidence stage yet; they are there for the guideline-check step. DGIdb's `latest` download is the June 2024
release.

If you imported an earlier version of this branch, drop the tables that are now queried live:
`DROP TABLE dgidb_interactions, clinpgx_clinical_annotations, clinpgx_annotation_alleles;`

Datasets considered but not imported yet:

- **Full ChEMBL database dump** (~35 GB in MySQL): the activity data (IC50, Ki…) is out of scope for now, and DGIdb
  already aggregates ChEMBL's drug–target interactions. The curated mechanism table is the part that adds value.
- **ClinVar**: variant–disease significance, not drug response, and millions of rows. Worth adding later as a
  "variant context" layer, filtered to the genes users actually query.
- **Open Targets, CIViC**: CIViC is already one of DGIdb's sources. Open Targets fits a later target–disease view.
- **TGA ARTG**: would answer "can this be supplied in Australia" beyond PBS subsidy.

## Workflow

There are three workflows, picked with the switch on the search card: **gene → drugs** and **drug → genes** (the
same stages, below) and **patient VCF + cancer type → candidate drugs** (Workflow 4, next section). Each runs six stages, and the
stage-by-stage trace is shown under the results header.

```
Gene or drug ──► POST /api/explore {gene | drug}               services/explorer.py runs the stages in order
                │
  1 Resolution  ├─ reference/normalise.py + workflow/resolution.py
                │    P53 → TP53, ENSG… / NCBI Gene ID → symbol (HGNC); one gene per search
                │    unknown or ambiguous name ⇒ HTTP 422 with the closest gene symbols; nothing else runs until the
                │    user picks one or chooses "search as typed" (only when HGNC data is imported)
  2 Profile     ├─ workflow/profiles.py     HGNC + UniProt (function, domains, location)
  3 Retrieval   ├─ services/biomed/aggregator.py   each source in its own try/except
                │    ├─ DGIdb (GraphQL)       drug–gene interactions, types, scores, PMIDs
                │    ├─ ChEMBL (local)        curated mechanisms of action with action types
                │    ├─ CPIC (local)          prescribing recommendations (only when SNP is selected)
                │    ├─ PharmGKB/ClinPGx      variant–drug clinical annotations, levels 1A–4 (only when SNP is selected)
                │    ├─ DrugBank              mechanism of action (needs a licensed key; skipped otherwise)
                │    ├─ PubTator3             related literature for the strongest pairs
                │    └─ PBS (local)           Australian listing (context only, not DGI evidence)
  4 Evidence    ├─ workflow/evidence.py     one row per gene–drug pair: merged, standardised relation types,
                │    evidence IDs + PMIDs, a grade and conflict flags; then "interactions found?"
                │      Strong      CPIC A, A/B, B · PharmGKB 1A/1B
                │      Moderate    PharmGKB 2A/2B · curated ChEMBL mechanism · DGIdb with 3+ databases and PMIDs
                │      Supportive  weaker CPIC / PharmGKB levels · DGIdb with 2 databases or PMIDs
                │      Limited     a single database record without literature
                │    nothing found ⇒ no LLM call; the page lists the sources checked
  5 Analysis    ├─ services/llm/report_generator.py
                │    the graded pairs are in the prompt, each listing only the evidence IDs the prompt contains;
                │    citation check: unknown IDs / no citations / truncated output → status=flagged
  6 Report      ▼
             frontend: trace · AI summary · gene profile · graded table / network · PGx · literature · PBS
```

The table, network, profile and grades are built from the source data by fixed rules, never by the LLM.
The LLM only writes the narrative, and every sentence must cite an evidence ID like `[E3]`.

After pulling this version, run `flask --app run init-db` once: it adds the gene-workflow prompt template (v3) and
activates it unless an admin-edited template is active. Older templates still work; the report sections are
appended to them.

## Workflow 4: patient VCF + cancer type → candidate drugs

A prototype for **lung cancer only**, with **synthetic test data only**: the form offers eight mock VCFs
(`backend/app/mock_vcfs/`, made by `backend/scripts/make_mock_vcfs.py`), and uploads must be confirmed as test data.
It is a hybrid design: deterministic tools do QC, tiering, evidence classification and ranking; LLM agents only
explore the exploratory track and explain.

```
VCF + cancer type ──► POST /api/vcf/explore                    services/vcf_explorer.py → variants/pipeline.py
                │
  1 VCF QC tool ├─ configurable (VCF_QC_*): FILTER = PASS, DP ≥ 20, ALT reads ≥ 5, VAF ≥ 5 %; germline: gnomAD AF
                │    > 1 % or present in the matched normal; annotation (file's VEP / ANNOVAR, plus Ensembl VEP REST
                │    for gnomAD, AlphaMissense, REVEL, CADD); protein-altering only. Failures go to an audit table.
  2 Resolution  ├─ HGNC / Entrez / UniProt, HGVS (EGFR p.L858R), OncoTree (LUAD)
  3 Tiering tool├─ Tier 1  CIViC A/B (OncoKB 1/2/R1) in this OncoTree subtype. A truncating variant in a tumour
                │          suppressor (IntOGen LoF) also matches gene-level evidence such as "STK11 Mutation" as a
                │          class; a missense never tiers through gene-level evidence
                │  Tier 2  cancerhotspots.org hotspot (oncogenes), a truncating mutation in a tumour suppressor
                │          driver of the subtype (TSGs: likely oncogenic as a class), or variant-level evidence in
                │          other cancer types
                │  Tier 3  S = 0.4·F + 0.3·R + 0.3·B (weights in VCF_TIER3_WEIGHTS), each component 0–1:
                │            F functional impact (AlphaMissense > REVEL > CADD; 0.9 for truncating)
                │            R subtype relevance (cBioPortal frequency in the subtype; 25 % counts as 1)
                │            B biological evidence (IntOGen cancer role 0.4, core oncogenic pathway 0.3, TSG loss 0.3)
                │    S below VCF_TIER3_MIN_SCORE -> "low priority" (kept and reported). No top-N cut here.
  4 Retrieval   ├─ every tiered variant: CIViC / OncoKB (variant level), DGIdb and ChEMBL (gene level)
  5 Evidence    ├─ merge, link drug -> variant -> sources, check co-occurring variants (L858R + T790M,
    classif.    │    T790M + C797S), flag resistance / conflicts, drop unsupported claims; two tracks:
                │      established  CIViC A/B (OncoKB 1/2) in this subtype for a Tier 1 variant, variant-specific
                │                   (exact / codon / exon); class-level drug evidence stays exploratory
                │      exploratory  levels C–E, other cancer types, DGIdb / ChEMBL-only links, all Tier 3 variants
  6 Agents (LLM)├─ exploratory track only; cannot change QC, tiers or levels (services/variants/agents.py):
                │      Literature (retrieval-augmented, see below: papers for Tier 3 variants and level C–E pairs,
                │      with the abstract sentences that answer each question) · Biological
                │      interpretation (pathways, co-mutations such as STK11 + KRAS, TP53 + RB1) · Hypothesis
                │    every statement cites an evidence ID or is labelled "Hypothesis"; a check flags violations
  7 Analysis    ├─ each track ranked separately by rules: established by level, subtype match, specificity,
                │    resistance (−60, down-ranked); exploratory by evidence × Tier 3 S + literature support.
                │    Top VCF_TOP_PER_TRACK drugs in the report, the full lists in the appendix. "No approved
                │    targeted therapy" when the established track has nothing without resistance. The Analysis
                │    agent (template vcf_report v3) explains both tracks; it runs in parallel with the agents.
  8 Report      ▼  A. established clinical evidence · B. exploratory predictions (scores, Tier 3 F/R/B/S, agent
                   output) · co-mutations without a targeted drug · network · appendix (all tiered, low-priority
                   and QC-filtered variants with reasons) · limitations · disclaimer
```

OncoKB is optional: set `ONCOKB_API_TOKEN` (free for academic use). COSMIC hotspots and the Cancer Gene Census need
a licence, so cancerhotspots.org and IntOGen gene roles are used instead. R counts every mutation, so large passenger
genes (TTN, MUC16) score high on R; B is there to offset this. SNVs / indels can't show fusions or copy-number
changes (ALK, ROS1, RET, MET amplification), which the report lists as a limitation.

### Literature retrieval (RAG) for the Literature agent

`services/variants/literature.py`, run in stage 6 before the agents. For each Tier 3 variant and level C–E pair:

1. **Search**: PubTator3 keyword search, 10 hits (`VCF_LITERATURE_CANDIDATES`).
2. **Fetch**: titles and abstracts with PubTator's entity annotations (one BioC export request per 100 papers).
3. **Entity gate**: the paper must name the gene (its NCBI Gene ID annotation or the symbol) and, for a drug
   question, the drug.
4. **Re-rank**: [MedCPT](https://github.com/ncbi/MedCPT) Cross-Encoder (NCBI, trained on PubMed search logs). Papers
   below `VCF_LITERATURE_MIN_SCORE` (0) are dropped: on-topic papers score above 0, off-topic ones around −10. The
   best 3 per question are kept (`VCF_LITERATURE_PER_QUERY`).
5. **Passages**: the 2 abstract sentences closest to the question (MedCPT Query / Article Encoders). The Literature
   agent gets these as `(S1)`, `(S2)` with the paper's evidence ID, and must base what it says on them, not on the title.

Only kept papers count as literature support in the ranking. The appendix lists them with their scores and the
sentences the agent read. MedCPT is optional and free (it runs locally):

```bash
pip install -r requirements-rag.txt   # PyTorch + transformers; the models (~1.3 GB) download on first use
```

Without it, or with `VCF_LITERATURE_RANKER=keyword`, papers and sentences are ranked by gene, variant, drug and
lung-cancer mentions instead. The first search after a restart loads the models (a few seconds; about 1–2 minutes the
first time, while they download). Tests always use the keyword ranker.

## Drug safety: interactions and warnings

Before a drug is shown, `services/safety.py` checks it with fixed rules (no LLM):

- **Warnings** (ChEMBL): a withdrawn drug (year, countries, toxicity class) or a boxed warning. In Workflow 4 a
  withdrawn drug loses `VCF_WITHDRAWN_PENALTY` points; boxed warnings are flagged only (most anticancer drugs carry one).
- **Drug–drug interactions** with the patient's current medications (optional field in every search form) and, in
  Workflow 4, between the drugs of each combination regimen. Four rules, strongest source first:
  1. *label class*: the drug's FDA label (openFDA) covers a class, e.g. "Strong CYP3A Inducers: Avoid concomitant use",
     and the FDA table puts the other drug in it (carbamazepine: strong CYP3A inducer);
  2. *label names drug*: the label names the other drug ("Avoid concomitant use of clopidogrel with omeprazole");
  3. *FDA table*: a strong / moderate inhibitor or inducer meets a sensitive substrate of the same pathway;
  4. *phenoconversion* (drug–drug–gene): the other drug strongly or moderately inhibits a pharmacogene for which the
     drug has a CPIC guideline (fluoxetine + clopidogrel: CYP2C19), so the functional phenotype differs from the
     genotype.

  Severity is *major* when the label says to avoid the combination (in that sentence or the next ones on the same
  pathway), when a strong modulator meets a sensitive substrate, or for strong-inhibitor phenoconversion. Sentences
  saying no adjustment is needed are not flagged. Interactions are **flagged, not scored**: the answer to
  "osimertinib + carbamazepine" is to change the carbamazepine, not to rank an unapproved drug above osimertinib.

Each alert is an evidence item (the LLM may cite it but not add others), a row of `query_drug_interactions`, a
"Drug safety" section on the results page and a table in the Word report. Labels come from openFDA (free; 1,000
requests a day per IP without `OPENFDA_API_KEY`) through the local response store, so `warm-cache` covers them.
Pharmacokinetic interactions only: additive toxicity or QT effects are not checked.

## In-silico mutagenesis heatmap (optional, AlphaGenome)

The last section of the Workflow 4 report maps every single-base change in a ±10 bp window around each variant that
passed QC (tiered and low-priority; 21 positions × 3 bases = 63 substitutions), scored by Google DeepMind's
AlphaGenome against the GRCh38 reference. By default the score is the RNA-seq log fold change for the variant's gene,
averaged over the lung tracks (`UBERON:0002048`); red raises it, blue lowers it, and the patient's own change is
outlined. The page asks for one variant at a time (`POST /api/vcf/ism`), as each map takes tens of seconds; results are
cached in memory.

```bash
pip install -r requirements-alphagenome.txt   # Python 3.11+
# backend/.env: ALPHAGENOME_API_KEY=...  (free non-commercial key: https://deepmind.google.com/science/alphagenome)
```

`ALPHAGENOME_ISM_OUTPUT` switches the score to splicing (`SPLICE_SITES`) or chromatin (`DNASE`, `ATAC`, `CAGE`),
`ALPHAGENOME_ISM_TISSUE` to another UBERON / CL term, `ALPHAGENOME_ISM_FLANK` the window. AlphaGenome models
regulation (expression, splicing, chromatin), not protein function, so for a missense variant the map is not a
pathogenicity call. Without a key the section says how to enable it.

## MLflow tracking (optional)

Set `MLFLOW_TRACKING_URI` (e.g. `sqlite:///mlflow.db` in `backend/.env`) and every search, in both workflows, is
logged by `app/tracking.py`:

- **A run** in the experiment `pharmagenix-gene` or `pharmagenix-vcf`: parameters (gene / sample, cancer type, QC
  thresholds, Tier 3 weights, template version, models), metrics (counts per stage, tiers, drugs per track, seconds
  per stage, LLM latency / tokens / citation checks per report role) and artifacts (`evidence.json` and every report).
  User ratings are added later as `user_rating_<role>` metrics.
- **A trace** of the same search: a span per workflow stage and per data source, and every OpenAI / Anthropic call
  (MLflow autolog), including the Analysis agent and the three exploration agents. Ratings are attached to the
  trace as assessments.

```bash
cd backend && .venv/bin/flask --app run mlflow-ui      # then open http://127.0.0.1:5050
```

It uses the project's own MLflow (a bare `mlflow` command is usually not on the PATH) and the same database as the
app; if the UI is already running it just prints the address. Runs are under Experiments → `pharmagenix-vcf` /
`pharmagenix-gene`; each run's Traces tab shows the stages and LLM calls.

Without `MLFLOW_TRACKING_URI` nothing is logged, and MLflow errors never fail a search.

### Evaluating Workflow 4

`app/evaluation.py` runs Workflow 4 on the eight mock VCFs and scores each result against an expected answer taken
from published evidence (CIViC, FDA labels, NSCLC guidelines), not from the pipeline's own output:

```bash
cd backend && .venv/bin/flask --app run mlflow-eval                  # deterministic stages only: ~1 min, no LLM cost
cd backend && .venv/bin/flask --app run mlflow-eval --llm            # full workflow, incl. reports and agents
cd backend && .venv/bin/flask --app run mlflow-eval --case mock_luad_kras_g12c   # one case (repeatable)
```

| Check | Passes when |
|---|---|
| `tiers_correct` | every expected variant is in its expected tier |
| `qc_filter_correct` | the QC artefacts are removed and the somatic variants kept |
| `expected_drugs_established` | the guideline drugs are on the established track, not down-ranked |
| `resistance_down_ranked` | drugs the sample is resistant to (e.g. gefitinib with T790M) are down-ranked |
| `established_not_contaminated` | the established track holds only drugs backed by a Tier 1–2 variant, none forbidden |
| `no_therapy_flag_correct` | "no approved targeted therapy" is shown exactly when expected |
| `report_citations_valid` (`--llm`) | the analysis report cites evidence, and only IDs it was given |
| `agent_statements_grounded` (`--llm`) | every exploration-agent statement cites evidence or is a hypothesis |

In the MLflow UI, experiment `pharmagenix-vcf`:

- **Datasets**: `pharmagenix-vcf-cases`, the inputs and expected answers.
- **Evaluation runs**: one row per case and one column per check. A failing row links to its trace and shows the
  check's reason.
- **Judges**: the checks, if `MLFLOW_SERVER_ENABLE_CUSTOM_SCORERS=true` is set in `backend/.env`. MLflow only
  stores code-based checks with this flag, because a stored check is code it runs later. Set it only for a
  tracking store you trust, such as the local `mlflow.db`, and restart the MLflow UI afterwards. The checks are
  scored with or without it.

To add a case, put a VCF in `app/mock_vcfs/` and its expected answer in `CASES` in `app/evaluation.py`.

## Project layout

```
backend/
  run.py                      Flask entry point
  schema.sql                  MySQL DDL generated from the models (reference only; init-db creates the tables)
  app/
    __init__.py               create_app: registers blueprints, serves the frontend static files
    config.py                 all settings (read from .env)
    models.py                 queries / reports / prompt_logs / prompt_templates / feedback / users
    cli.py                    flask init-db / import-data / reference-status / mlflow-ui / mlflow-eval / dump-schema
    tracking.py               MLflow runs, traces and feedback (optional)
    evaluation.py             MLflow evaluation of Workflow 4 on the mock VCFs
    routes/
      explore.py              /api/meta, /api/explore, /api/queries/<id>[/ask]
      vcf.py                  /api/vcf/options, /api/vcf/examples/<id>, /api/vcf/explore (Workflow 4)
      feedback.py             /api/reports/<id>/feedback
      auth.py                 /api/auth/* (user accounts: register, login, logout, me)
      admin.py                /api/admin/* (X-Admin-Token auth)
    reference/                local datasets
      models.py               genes, gene_aliases, drugs, drug_synonyms, drug_mechanisms, cpic_*, pbs_items,
                              source_releases
      importers/              hgnc, chembl, cpic, clinpgx, dgidb, fda, oncotree, intogen, civic, hotspots, cbioportal, pbs
      normalise.py            input → standard gene / drug identifiers
      lookup.py               local evidence queries
      text.py                 name normalisation (case, whitespace, salt forms)
    services/
      explorer.py             orchestrates one gene search through the workflow stages
      vcf_explorer.py         orchestrates Workflow 4 (patient VCF)
      variants/               Workflow 4: parse (VCF / MAF), vep, oncokb, cancer_types, matching, biology,
                              pipeline (deterministic tools), agents (exploration agents, LLM),
                              alphagenome (in-silico mutagenesis heatmap, optional)
      report_docx.py          "Download report (.docx)": the Word report of a query
      safety.py               drug warnings and drug–drug interaction checks (FDA labels, FDA CYP table, CPIC)
    report_assets/            report_base.docx: styles, page layout, header and footer of the Word report
    mock_vcfs/                synthetic mock VCFs + manifest.json (Workflow 4 test data)
  scripts/make_mock_vcfs.py   regenerates the mock VCFs (positions from Ensembl VEP)
      workflow/               one module per stage: resolution, profiles, evidence
      relations.py            relationship types and the DGIdb label mapping
      prompts.py              default template, evidence formatting, template rendering
      biomed/                 dgidb.py, pharmgkb.py, pubtator.py, drugbank.py, uniprot.py (live),
                              aggregator.py (runs stages 2–4)
      llm/                    openai_client.py, anthropic_client.py, report_generator.py
  tests/                      pytest
frontend/
  index.html  admin.html
  css/style.css
  js/ api.js  main.js  vcf.js  report.js  safety.js  auth.js  background.js  profile.js  workflow.js  summary.js  table.js  network.js  availability.js  admin.js  util.js
```

## MLflow
run cd backend && .venv/bin/flask --app run mlflow-ui
open http://127.0.0.1:5050 for MLflow logbook
cd backend && .venv/bin/flask --app run mlflow-eval
cd backend && .venv/bin/flask --app run mlflow-eval --llm
cd backend && .venv/bin/flask --app run mlflow-eval --case mock_luad_kras_g12c

 
## API reference

| Method   | Path                                      | Description                                                                                                           |
| -------- | ----------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| GET      | `/api/meta`                               | Relationship types, model info, imported reference data versions                                                      |
| POST     | `/api/explore`                            | `{gene, relation_types[], compare, confirm_unresolved}`, or `{entity_type: "drug", drug, ...}`; one name per search   |
| GET      | `/api/vcf/options`                        | Workflow 4 form: lung cancer types (OncoTree), the synthetic mock VCFs, QC thresholds                                 |
| GET      | `/api/vcf/examples/<id>`                  | Download one mock VCF                                                                                                  |
| POST     | `/api/vcf/explore`                        | Multipart `file` (VCF / VCF.gz), `cancer_type`, `test_data=1`, `compare`; or JSON `{example, cancer_type, compare}`   |
| GET      | `/api/queries[?mine=1]`                   | Recent queries; `mine=1` = the logged-in user's own history (401 when logged out)                                     |
| GET      | `/api/queries/<id>`                       | Fetch the full result of a past query, plus `entity_details` (HGNC / ChEMBL fields for the results header)            |
| POST     | `/api/queries/<id>/ask`                   | Follow-up / re-ask `{followup_question, compare}`; reuses the evidence when the gene and relation types are unchanged  |
| POST     | `/api/reports/<id>/feedback`              | `{rating 1–5, comment, suggestion}`                                                                                   |
| POST     | `/api/auth/register`, `/api/auth/login`   | `{email, password}` (email only, case-insensitive); sets a signed session cookie                                      |
| POST     | `/api/auth/logout`                        | Clear the session                                                                                                     |
| GET      | `/api/auth/me`                            | The signed-in user, or `null`                                                                                         |
| GET      | `/api/admin/prompt-logs[/<id>]`           | Prompt logs (filter by status / provider)                                                                             |
| GET/POST | `/api/admin/templates`                    | List templates / save a new version                                                                                   |
| POST     | `/api/admin/templates/<id>/activate`      | Switch the active template                                                                                            |
| POST     | `/api/admin/templates/preview`            | Render a template against a stored query's evidence (no LLM call)                                                     |
| GET      | `/api/admin/failures`                     | Failed and flagged reports                                                                                            |
| POST     | `/api/admin/reports/<id>/retry`           | Regenerate with the current template                                                                                  |
| GET      | `/api/admin/feedback`, `/api/admin/stats` | User feedback; per-model success rate, latency and average rating                                                     |

## Things to check and known limitations

- **Models and temperature**: the primary model is Claude Sonnet 4.6 at temperature 0.3, so the evidence summaries
  vary little between runs. It is the newest Claude that still accepts a temperature (Claude 4.7+ and 5.x reject one).
  `gpt-6-luna` is a reasoning model and rejects any temperature, so `OPENAI_TEMPERATURE` stays empty. Swap the two
  with `PRIMARY_PROVIDER` / `COMPARISON_PROVIDER`. The anthropic SDK 1.x has no `temperature` argument, so the client
  sends it in the request body (`extra_body`).
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
  20 seconds, so the importer only fetches the current month (four requests: schedule, items, restrictions, item–restriction links). Register at
  <https://data-api-portal.health.gov.au> for your own key (`PBS_SUBSCRIPTION_KEY`). PBS matching is by generic name
  or ChEMBL ID, so combination products may not be matched. Restriction text is shown as published (the PBS licence
  does not permit modification); whether a patient meets a restriction is not checked.
- Requests are synchronous (20–60 s each). Under real load, move `run_exploration` into a Celery/RQ task and have the
  frontend poll.
- The admin console uses a single shared token. Replace it with real accounts before deploying publicly.
- Schema changes currently rely on `init-db` (`db.create_all` does not alter existing tables). Add Flask-Migrate once
  the models settle.
