# Pharmagenix user manual

Pharmagenix is a GenAI-powered drug–gene interaction (DGI) explorer. It answers three kinds of question:

| Workflow | You enter | You get |
| --- | --- | --- |
| 🧬 **Gene → drugs** | a gene (e.g. CYP2C19, or an alias such as P53) | every drug that interacts with it, graded by evidence |
| 💊 **Drug → genes** | a drug (generic or brand name, e.g. clopidogrel or Plavix) | every gene it acts on or depends on: targets, metabolising enzymes, transporters, pharmacogenes |
| 🧪 **Patient VCF → candidate drugs** | a tumour's somatic variants (VCF / MAF) and its lung cancer type | ranked candidate drugs for the actionable variants, split into established evidence and exploratory predictions |

Every answer has an LLM-written summary in which each claim cites its evidence (`[E3]`), a structured table, an
interactive network and the source records. Users can rate answers, suggest improvements, ask follow-up questions and download the result as a Word report.

**Research and teaching use only.** Pharmagenix is not clinical advice. The VCF workflow is a lung cancer prototype
and accepts synthetic test data only.

## Sample logins

| Account | Email | Password | Can do |
| --- | --- | --- | --- |
| Researcher | `demo@pharmagenix.test` | `Demo1234!` | Search, rate answers, ask follow-ups, see their own query history |
| Administrator | `admin@pharmagenix.test` | `Admin1234!` | All of the above, plus the admin console |

Create (or reset) them with `flask --app run seed-demo`. The admin console also accepts the `ADMIN_TOKEN` from
`backend/.env`.

**What needs a login.** A plain gene or drug search works without an account. Anything with patient data needs one:
a patient VCF, current medications or genotypes. Such a result is private: only the account that made it, and
administrators, can open it, and a shared link to it asks the viewer to log in. Logging out clears any result from
the screen.

## Starting the system

Requirements: Python 3.12, MySQL 8, an Anthropic API key (primary model, Claude Sonnet 4.6) and optionally an OpenAI key (comparison
reports), all set in `backend/.env` (copy `backend/.env.example`).

```bash
cd backend
source .venv/bin/activate
flask --app run init-db          # first time: create the tables (an existing database: flask --app run upgrade-db)
flask --app run import-data      # first time: load the reference datasets (about 4 minutes)
flask --app run seed-demo        # the sample logins above
flask --app run run --debug --host 127.0.0.1 --port 5001
```

Open <http://127.0.0.1:5001> for the explorer and <http://127.0.0.1:5001/admin> for the admin console. The full
installation (MySQL user, Python packages, optional MedCPT) is in the README.

## Logging in and query history

Click **Log in** (top right). Enter one of the sample logins, or choose **Create account** to make your own (any
email, password of 8+ characters).

When logged in, the same button shows your email. Click it to see **My recent queries**: your last 10 searches,
newest first. Click one to reopen its stored result without searching again. Administrators also see a link to the
**Admin console**.

## 🧬 Gene → drugs

1. Make sure **Gene → drugs** is selected, type a gene in the search box (or click an example such as **CYP2C19**)
   and press **Explore**.
2. Optionally untick relationship types under **Relationship types**: Inhibition, Induction / activation, Binding,
   Metabolism, SNP-specific response and Unspecified. Only interactions of the ticked types are kept.
3. Optionally tick **Generate comparison report (with gpt-6-luna)** to get a second report from another model.

A search takes 20–60 seconds. If the name is unknown or ambiguous (e.g. a typo, or an alias shared by two genes),
the search stops and suggests the closest gene symbols: pick one, or choose **Search as typed anyway**.

The results page has:

- **Header**: the resolved gene (e.g. "P53 → TP53 (HGNC alias)"), its identifiers with links to HGNC, NCBI Gene,
  UniProt and Open Targets, headline numbers, and which sources answered.
- **Workflow trace**: what each stage did (resolution, profile, retrieval, evidence integration, report).
- **AI summary**: the LLM report. Click a citation such as `[E3]` to jump to the evidence behind it. Rule-based
  alerts (CPIC level A/B guidelines, PharmGKB level 1A/1B annotations) are shown above it and are not written by
  the AI. **Cited evidence** at the bottom lists every cited source with a link.
- **Gene profile**: function, domains and location from HGNC and UniProt.
- **Drug–gene interactions**: one row per gene–drug pair with its evidence grade (strong / moderate / limited /
  supportive), relationship, sources and PubMed IDs. Filter it, show unspecified interactions, or **Export CSV**.
  Switch to **Network** to see the same pairs as a graph; edges are coloured by relationship type.
- **Pharmacogenomics**: CPIC prescribing guidelines with genotype-specific recommendations, and PharmGKB clinical
  annotations.
- **Literature**: related PubMed papers found by PubTator.
- **Australian availability (PBS)**: whether each drug is on the PBS Schedule, its item codes, benefit type and the
  full restriction text (as published; whether a patient meets it is not checked).

## 💊 Drug → genes

Select **Drug → genes** above the search box, then type a drug. Generic names, salt forms (clopidogrel bisulfate)
and brand names (Plavix) all work. The results page has the same sections as a gene search, seen from the drug's
side:

- The header and **Drug profile** show the ChEMBL record: molecule type, clinical phase (4 = approved), first
  approval year, brand names and mechanism of action.
- **Drug–gene interactions** lists the genes: drug targets (e.g. P2RY12 for clopidogrel), metabolising enzymes and
  pharmacogenes (CYP2C19), transporters.
- **Pharmacogenomics** shows which gene variants change the drug's response, toxicity or dose.

Try **clopidogrel**: CYP2C19 comes out as the strongest pair (CPIC level A, SNP-specific response), matching the
CPIC guideline. **codeine** gives CYP2D6.

## 💊 Current medications and drug safety

Every search form has an optional **Current medications** field (comma-separated, generic or brand names, e.g.
`omeprazole, fluoxetine`). The results page then has a **Drug safety** section:

- **Interactions**: each drug of the result checked against the medications, with a severity badge (*major*: avoid or
  change the combination; *moderate*: monitor or adjust the dose; *minor*), the finding, the quoted sentence from the
  drug's FDA label and the source. A *phenoconversion* alert means the medication inhibits a gene the drug has a CPIC
  guideline for: for example fluoxetine makes a CYP2C19 normal metaboliser respond like a poor metaboliser to
  clopidogrel, so the genotype-based recommendation may not apply.
- **Warnings** (ChEMBL): withdrawn drugs and boxed warnings, also shown as badges in the interaction table.

In the VCF workflow the drugs inside each combination regimen are always checked against each other. Interactions are
flagged, not used to re-rank: change the co-medication or adjust the dose. Withdrawn drugs are down-ranked. Try it:
search **Plavix** with current medications `omeprazole, fluoxetine`, or run the EGFR L858R + T790M example with
`carbamazepine`.

The check covers pharmacokinetic interactions found in FDA labels and the FDA CYP / transporter table; it is not a
complete interaction checker (additive toxicity, QT prolongation and food interactions are not covered).

## 🧬 Patient genotypes → CPIC recommendation

The gene and drug search form has an optional **Patient genotypes** field, one result per gene separated by `;`:
a diplotype (`CYP2C19 *2/*2`, either allele order), a phenotype (`CYP2D6 poor metabolizer`, or `PM` / `IM` / `NM` /
`RM` / `UM`) or an HLA allele status (`HLA-B*57:01 negative`). The app:

1. maps each diplotype to its CPIC phenotype (CPIC's diplotype tables);
2. for every drug in the result with a CPIC guideline for that gene, shows the CPIC recommendation for exactly that
   phenotype, per population (e.g. clopidogrel: ACS/PCI, non-ACS, neurovascular), with its strength. Guidelines that
   need two genes (amitriptyline: CYP2C19 + CYP2D6) say which result is still missing;
3. flags *phenoconversion* when a current medication strongly or moderately inhibits the gene (fluoxetine and
   CYP2D6): the patient may respond like a poorer metaboliser than the genotype says.

The results appear at the top of **Pharmacogenomics**, in **At a glance**, in the AI report (cited like any other
evidence) and in the Word report (page 1 and Section 3.4). Try it: search **clopidogrel** with genotype
`CYP2C19 *2/*2` and current medication `omeprazole`.

## 🗂 Result tabs: Clinical, Extra, Export

Every result opens on **Clinical**, the view for a clinic visit: At a glance, the AI summary's first paragraph, the
patient-genotype recommendations and drug safety (for a VCF: the Clinical summary, the established treatment options
and drug safety). **Extra** holds everything else: the workflow trace, the full AI report with follow-up questions,
the profile, the interaction table and network, pharmacogenomic evidence, literature and PBS details (for a VCF: the
Analysis agent, exploratory predictions, co-mutations, network, appendix, limitations and ISM heatmap). **Export** has
the Word report, the CSV of the interaction table and a link to the result. Clicking a citation such as [E12] in the
Clinical view opens Extra at that evidence.

## ⚡ At a glance

Above the AI summary, a short rule-based box (not written by the AI) gives one line per decision: the genotype
recommendation, the best-supported drugs or genes with their PBS status, the worst drug interaction and any warnings.
For a patient VCF it shows the first targeted option for consideration (evidence level, variants, PBS), the
alternatives, the drugs not recommended because of resistance, and the worst interaction. When no established
targeted therapy is found, a **Next step** line points to standard of care and names the top exploratory hypothesis,
with a link to the Exploratory track in the Extra tab. Each line links to its evidence.

## 🧪 Patient VCF → candidate drugs

1. Log in, then select **Patient VCF → candidate drugs**.
2. Upload your own VCF / MAF file and tick **I confirm this file is synthetic test data**. Administrators also see a
   **Synthetic example** tab with the eight mock VCFs; other users, and visitors who are not logged in, see only the
   upload form.
3. Choose the cancer type (e.g. LUAD; you can also type "lung adenocarcinoma").
4. Optionally enter **Current medications** and open **Clinical context** (stage, treatment status, age, sex,
   smoking, tumour purity, ECOG, brain metastases, other context). The clinical context is used only by the Clinical
   summary agent and the Word report; it never changes QC, tiers or scores. Fields left empty are listed in the report
   as "not provided".
5. Press **Predict candidate drugs**. This takes about 30–90 seconds.

| Example | Cancer type | What it shows |
| --- | --- | --- |
| EGFR L858R + T790M, with QC artefacts | LUAD | Osimertinib first; first-generation EGFR inhibitors down-ranked by T790M; low-quality and germline variants filtered with reasons |
| EGFR L858R + T790M + C797S | LUAD | Osimertinib resistance from C797S |
| EGFR exon 19 deletion + TP53 / CDKN2A | LUAD | Exon-level matching |
| KRAS G12C + STK11 / KEAP1 | LUAD | KRAS G12C inhibitors; STK11 loss and PD-1 inhibitor resistance |
| BRAF V600E + TP53 | LUAD | BRAF / MEK inhibitors |
| PIK3CA / NFE2L2 / TP53 | LUSC | No approved targeted therapy |
| TP53 + RB1 | SCLC | No approved targeted therapy |
| Only Tier 3 candidates | LUAD | Exploratory predictions only |

The results page has:

- **Overview**: the workflow trace, the Analysis agent's report and the **Clinical summary** (AI): recommended
  first-line options, drugs not recommended, what to confirm before treating, the regimen comparison table and a
  monitoring plan. Statements cite evidence ([E3]) or are marked **[GK]**: general clinical knowledge (e.g. a standard
  chemotherapy partner or a monitoring interval) that is not in the retrieved evidence and must be checked against
  current guidelines. A check flags the summary if it recommends a down-ranked drug or a regimen containing one.
- **A. Established clinical evidence**: drugs with CIViC level A/B (or OncoKB level 1–2) evidence in this cancer
  type, with the score breakdown, PBS status and references. **Drugs to avoid** lists drugs with resistance evidence
  only.
- **B. Exploratory predictions**: lower-level evidence, other cancer types, gene-level links and Tier 3 variants,
  with the Literature, Biological interpretation and Hypothesis agents' notes. These are predictions, not evidence
  of response.
- **Co-mutations without a targeted drug**, the **Variant–drug network** and **Limitations**.
- **Appendix**: every tiered and low-priority variant, the QC audit table (each filtered variant and why), the full
  drug lists, and **Retrieved literature**: the papers the Literature agent read, ranked by MedCPT, with the abstract
  sentences it was given.
- **In-silico mutagenesis (AlphaGenome)**, at the bottom (see below).

### In-silico mutagenesis heatmap (AlphaGenome)

The last section maps how every possible single-base change around a variant would affect the gene, as predicted by
Google DeepMind's AlphaGenome model. It covers the variants that passed QC (tiered first, then low priority).

1. The first variant is scored automatically; press another variant's button to score it. Each takes 10–60 seconds
   the first time, then it is cached until the server restarts.
2. Read the grid: columns are genomic positions (±10 bp around the variant, its own position in bold), rows are the
   four bases. Each cell is the predicted change if that position were mutated to that base. Grey **ref** cells are
   the reference base. Red means the change raises the score, blue means it lowers it. The cell with a black outline
   is the patient's own change.
3. By default the score is the predicted RNA-seq expression change (log fold change) of the variant's gene in lung
   tissue. The line above the grid says which output, tissue and gene were used.

How to interpret it:

- AlphaGenome predicts gene regulation (expression, splicing, chromatin) from the DNA sequence. It does not model
  protein structure or function. For a missense variant such as EGFR L858R, the values are therefore small (around
  0.001–0.005). That is expected: the change alters the protein, not how much of it is made. Values like these do
  not say whether the variant is pathogenic or drug-sensitive; Sections A and B do that.
- The map is most informative for variants near splice sites, promoters or other regulatory sequence. To see
  splicing effects instead, set `ALPHAGENOME_ISM_OUTPUT=SPLICE_SITES` in `backend/.env` and restart.
- GRCh38 only. Without `ALPHAGENOME_API_KEY` the section explains how to enable it; the rest of the page is
  unaffected.

## Downloading a report (.docx)

Every results page (gene, drug or patient VCF) has **Download report (.docx)** at the top of the overview. All reports
follow the same structure (the team's *Candidate Drug Report*, see `docs/report_templates`):

- **Page 1**: a case table (patient VCF: sample, cancer type, clinical context, medications, what was not provided;
  gene / drug: the query and data sources) and **Brief Results** (VCF: recommended options, not recommended, confirm
  before treating, co-mutations, at progression).
- **Abstract**, **1 Introduction**, **2 Methodology** (QC, tiering, evidence levels, tracks, scoring and safety rules,
  with worked score examples), **3 Results** (QC steps, variants, the variant × drug evidence heat map as Figure 1,
  established and exploratory tracks with a score heat map, co-mutations and the network), **4 Interpretation** (the
  Analysis agent; for a VCF also the regimen table and monitoring plan from the Clinical summary agent),
  **5 Limitations**, **6 Conclusion** and **References** (the evidence cited plus the methods and databases used).
- **Appendices**: the exploration agents' notes and the QC audit (VCF), and the sources and provenance of the query
  (time, models, prompt template versions, citation checks).

Gene → drugs and Drug → genes reports use the same skeleton with the sections that apply (graded pairs, evidence heat
map, CPIC, PharmGKB, PBS). Everything except the text marked as written by a language model is built from the stored
results by fixed rules, so the document always matches the page and can be downloaded again later from **My recent
queries**. [GK] statements stay marked in the document. File name: `Pharmagenix_report_PGX-<query>_<gene>.docx`.

## Feedback and follow-up questions

Under every report:

- **Answer quality**: give 1–5 stars, a comment and a suggestion, then **Submit feedback**. Ratings are averaged per
  report, and administrators see them per model. Each person has one rating per report: submitting again (the button
  then reads **Update feedback**) replaces your earlier rating instead of adding a second one.
- **Ask a follow-up or regenerate**: type a question (e.g. "why is gefitinib down-ranked for this sample?") and
  submit. The stored evidence is reused, so no source is queried again, and a new report answers the question.

## Admin console

Open <http://127.0.0.1:5001/admin> while logged in as the administrator (or enter the `ADMIN_TOKEN`). Tabs:

- **Prompt logs**: every LLM call with the exact prompt sent, the raw response, model, status, latency and tokens.
  Filter by status.
- **Templates**: the prompt templates for each workflow. Editing creates a new version, so earlier reports stay
  reproducible; activate a version to use it for new searches.
- **Failed / flagged**: reports that failed, or that a quality check flagged (e.g. a citation to evidence that was
  not in the prompt, a statement without a citation). **Retry** re-runs one.
- **User feedback**: ratings, comments and suggestions.
- **Model comparison**: success rate, latency, tokens and average rating per model, and the loaded reference
  data versions.
- **Evaluation**: how well Workflow 4 does on the 8 mock VCFs: precision, recall, F1, specificity and a confusion
  matrix for the drug recommendations, a confusion matrix and per-tier F1 for variant tiering, a per-case table
  and the limitations of the test set. Refresh it with `flask --app run eval-metrics` (a few minutes, no LLM cost).

## Before a demo

1. `flask --app run warm-cache` runs every example search above once, without the LLM, and stores the API answers
   in `backend/http_cache.sqlite`. From then on, if a public API (e.g. Ensembl VEP) is down, the stored answer is
   used automatically and the results page says which source it came from.
2. With no network at all, set `HTTP_CACHE=offline` in `backend/.env` and restart: the example searches run from the
   stored answers. The LLM reports still need the internet; earlier results can always be reopened from **My recent
   queries**.
3. Log in as `demo@pharmagenix.test` beforehand and run the searches you will show, so they are in the history.
4. If you will show the ISM heatmap, open each VCF result once beforehand so the heatmaps are cached (the cache is
   lost when the server restarts), and download each Word report once to check it opens.

## Troubleshooting

| Message | Cause and fix |
| --- | --- |
| "Ensembl VEP, the variant annotation service, is not responding right now" | The public Ensembl server is down or slow, and this file has no stored answer. Try again in a few minutes, or run `warm-cache` beforehand. |
| "… was not found. Pick a suggestion or search as typed." | The name is not in the local HGNC / ChEMBL data. Pick a suggestion, or search as typed to send it to the live sources unchanged. |
| A source pill shows "failed" | That source did not answer; the others still did. The report says the result may be incomplete. |
| "OncoKB not enabled" | No `ONCOKB_API_TOKEN` in `backend/.env`; CIViC is used alone. |
| "PBS data has not been imported" | Run `flask --app run import-data pbs`. |
| Comparison report: "API key not configured" | Add `OPENAI_API_KEY` to `backend/.env`. |
| Drug safety: "openFDA … failed" | The FDA label service did not answer (or the 1,000-a-day limit without a key was reached). The FDA-table and CPIC rules still ran; set `OPENFDA_API_KEY` or run `warm-cache` before a demo. |
| No drug warnings anywhere | The ChEMBL import predates the warnings: run `flask --app run import-data chembl`. |
| ISM section: "AlphaGenome is not configured on this server" | Add `ALPHAGENOME_API_KEY` to `backend/.env` and run `pip install -r requirements-alphagenome.txt`, then restart. |
| ISM heatmap: "AlphaGenome request failed" | The AlphaGenome service is down or the key's quota is used up. The rest of the report is unaffected; try again later. |
