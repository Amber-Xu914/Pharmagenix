# Evaluation

Two evaluations, both reproducible from the repository:

1. **Deterministic pipeline accuracy**: the QC, tiering and drug-track stages of Workflow 4 (no LLM) against expected
   answers taken from the published evidence (CIViC, FDA labels, NCCN guidance for NSCLC).
2. **LLM benchmark**: does grounding the LLM in retrieved, graded evidence make its reports more verifiable than the
   same model answering alone? Three arms on 18 cases, automatic metrics, paired statistics.

## 1. Deterministic pipeline accuracy

`flask --app run mlflow-eval` (backend/app/evaluation.py) runs the 8 synthetic mock VCFs through stages 1–7 and scores
six checks per case. Results (MLflow run `dcb610d739a44c07b8ec20a9df7d1572`, 11 November 2026):

| Check | What it tests | Pass rate (8 cases) |
| --- | --- | --- |
| `tiers_correct` | every variant gets its expected tier | 100% |
| `qc_filter_correct` | low-quality and germline variants are removed, the rest kept | 100% |
| `expected_drugs_established` | e.g. osimertinib for EGFR L858R + T790M, sotorasib / adagrasib for KRAS G12C | 100% |
| `resistance_down_ranked` | e.g. gefitinib / erlotinib with T790M, osimertinib with C797S | 100% |
| `established_not_contaminated` | no drug from a QC artefact or a Tier 3 variant on the established track | 100% |
| `no_therapy_flag_correct` | "no approved targeted therapy" shown exactly when expected (LUSC, SCLC, Tier 3 only) | 100% |

## 2. LLM benchmark: grounded vs. ungrounded

`flask --app run benchmark` (backend/app/benchmark.py). Full results: [benchmark.md](benchmark.md); per-case values:
`benchmark_cases.csv`; the 54 report texts: `benchmark_reports.json`.

**Design.** 18 cases: the 8 mock VCFs and the 10 demo searches (CYP2C19, CYP2D6, BRAF, TPMT, DPYD; clopidogrel, Plavix,
codeine, osimertinib, warfarin). The evidence for each case is built once, so the arms differ only in the model and in
whether it sees the evidence:

| Arm | Model | Prompt |
| --- | --- | --- |
| grounded-primary (the deployed system) | Claude Sonnet 4.6, temperature 0.3 | active template + graded evidence, cite evidence IDs |
| grounded-comparison | gpt-6-luna | the same prompt and evidence |
| ungrounded-primary (baseline: "ask the LLM") | Claude Sonnet 4.6 | the same question and sections, no evidence; cite PMIDs |

**Results** (mean ± SD; p from paired Wilcoxon signed-rank tests against grounded-primary, Bonferroni-corrected over
14 tests). The grounded arms ran with a 4,000-token output limit and the ungrounded arm with 8,000; no report reached
its limit (longest: 2,776, 2,966 and 7,118 tokens).

| Metric | Grounded Claude | Grounded GPT | Ungrounded Claude |
| --- | --- | --- | --- |
| Verifiable citations | 100% | 100% | 99% of PMIDs exist |
| ...of which on topic (PubMed title names the case's genes / drugs) | – | – | **36% ± 21%** |
| Sentences with a citation | 65% ± 15% | **93% ± 7%** (p < 0.001) | 47% ± 14% (p = 0.001) |
| Drugs named that no evidence record names | 3% ± 12% | 0% | **51% ± 32%** (p < 0.001) |
| Expected answer recall | 96% | 93% (n.s.) | 93% (n.s.) |
| Resistant / non-established drugs recommended without a warning | 0 | 0 | 0 |
| Latency | 30 s | **20 s** (p < 0.001) | 88 s (p < 0.001) |
| Output tokens | 1,480 | 1,990 (n.s.) | 4,780 (p < 0.001) |

**Findings.**

- *Grounding makes the report checkable without making it less complete.* The ungrounded model recalled the expected
  answers about as often (93% vs 96%, not significant), but half of the drugs it named (51%) are in none of the case's
  evidence records, against 3% with grounding, and fewer of its sentences carry a citation (47% vs 65%).
- *An LLM's own references look valid but mostly are not.* The ungrounded arm cited 280 distinct PMIDs; 276 exist in
  PubMed (4 do not: 19667575, 21121864, 24164969, 33097553), so a check of "does the PMID exist" would pass it. But in
  only 36% of them does the PubMed title name the case's genes or drugs. Three examples, checked by hand against
  PubMed:
  - TPMT report: "Low TPMT activity → excess TGNs → myelosuppression [PMID 8552004]". PMID 8552004 is *Acute
    gestational cocaine exposure alone or in combination with low-dose ethanol does not influence prenatal mortality or
    fetal weight in mice*.
  - CYP2D6 report: desipramine, "Hydroxylation [PMID 9048568]". PMID 9048568 is *Analysis of hydroperoxide-induced
    tyrosyl radicals and lipoxygenase activity in aspirin-treated human prostaglandin H synthase-2*.
  - DPYD report: uracil, "competes with 5-FU for DPD [PMID 9536088]". PMID 9536088 is *Mutations of the
    flavin-containing monooxygenase gene (FMO3) cause trimethylaminuria, a defect in detoxication*.

  In the grounded arms every citation points to a record the user can open, and the system checks each one
  automatically (100% valid in both models).
- *Model choice.* With the same evidence, gpt-6-luna cites more sentences (93% vs 65%) and is faster (20 s vs 30 s);
  recall and evidence use do not differ significantly. Claude remains the primary model for its temperature control
  (gpt-6-luna is a reasoning model and accepts none); the comparison report lets users see both.
- *Safety.* No arm presented a drug that the expected answer marks as resistant or not established as a recommendation
  without a warning, in any of the 8 VCF cases.

**Limitations.**

- 18 cases and one run per arm; the metrics are automatic and no clinician rated the reports.
- "On topic" is a title-only check: it under-counts relevant papers whose titles use other names (the BRAF report's
  PMID 15520807, *The RAF proteins take centre stage*, is relevant but counted off topic), so 36% is a lower bound on
  relevance; it does not test whether a paper supports the specific claim.
- The ungrounded arm was first run with the 4,000-token limit, where 13 of its 18 reports were cut off; it was re-run
  with 8,000 (`benchmark --arm ungrounded-primary --max-tokens 8000`) and the figures above are from that run. The
  grounded arms were not re-run: none of their reports came near the limit.
- "Drugs not in the evidence" measures what the curated sources do not back, not what is false: the grounded codeine
  report names morphine, its active metabolite, from the model's own knowledge.
- The expected answers for the gene / drug cases are small lists of CPIC level A pairs and label facts
  (`benchmark.GOLD`).

## Model choice and next steps

**Current choice (kept for the submission).** The primary model is Claude Sonnet 4.6 at temperature 0.3; the comparison
model is gpt-6-luna. Sonnet 4.6 was chosen because it is the newest Claude model that still accepts a `temperature`,
which keeps the narrative close between runs. With the same evidence, the benchmark found no significant difference
between the two models in citation validity, use of the evidence or expected-answer recall; gpt-6-luna cites more
sentences (93% vs 65%) and answers faster (20 s vs 30 s). The deterministic pipeline, not the LLM, decides tiers,
grades, scores and rankings, so the model choice affects only the wording of the reports.

**Constraints found while evaluating (November 2026).**

- *Newer Claude models reject sampling parameters.* Claude 4.7 and later models, including the current Claude Sonnet
  5.5 and Claude Opus 5.5, return a 400 error when a request sets `temperature`, `top_p` or `top_k`.
- *So will Sonnet 4.6, on the next API version.* On API version `2026-10-21`, any request that carries these
  parameters is rejected on every model. The Python SDK sends that version by default from 2.0.0 (PyPI had 2.8.0 at
  the time of writing); this project uses 1.8.0, and `requirements.txt` now pins `anthropic>=1.8,<2` so that a fresh
  install (e.g. on the lab machines) keeps working. The pin postpones the change; it does not remove it.
- *Sonnet 4.6 is no longer the newest or the cheapest Sonnet.* Anthropic list prices per million input / output
  tokens (October 2026): Claude Sonnet 4.6 $3 / $15; Claude Sonnet 5.5 $2 / $10; Claude Opus 5.5 $4 / $20 (the
  general-purpose default); Claude Fable 5.1 $10 / $50 (the most capable, for the hardest reasoning tasks; its turns
  can take minutes).

**Possible next steps, in order of effort.**

1. *Measure how much temperature matters.* Run the grounded arm twice on the same evidence, with and without
   `temperature`, and compare run-to-run consistency (overlap of the cited evidence IDs, the drugs named, and the
   metrics above). If the difference is small, the main reason for staying on Sonnet 4.6 goes away. This would add a
   consistency metric to `benchmark.py`.
2. *Benchmark a newer model on the same cases.* Set `ANTHROPIC_MODEL` to `claude-sonnet-5-5` (cheaper) or
   `claude-opus-5-5`, with `ANTHROPIC_TEMPERATURE` empty, and re-run only the grounded arm:
   `flask --app run benchmark --arm grounded-primary` (18 calls; the other arms' saved reports are kept and the paired
   tests are recomputed). Compare citation validity, sentence coverage, drugs outside the evidence, expected-answer
   recall, the exploration agents' grounding checks (`mlflow-eval --llm`), latency and cost per report.
3. *Migrate if it holds up.* Remove `temperature` from the Claude client (`services/llm/anthropic_client.py`) and its
   setting, steer depth with the `effort` setting instead (lower effort for a shorter, more uniform narrative; Opus 5.5
   defaults to `medium`, Sonnet 5.5 to `high`), and raise `LLM_MAX_TOKENS` above 4,000, since these models think
   before answering and the thinking counts toward the output limit. Then lift the SDK pin to 2.x and re-run the
   tests and the benchmark.
4. *Alternatively, promote gpt-6-luna.* It already runs without a temperature and cited more sentences, but 18 cases
   are too few to switch on that alone; it would need the same benchmark plus the agents' checks.

## Reproducing

```bash
cd backend
flask --app run mlflow-eval                 # deterministic checks (needs MLFLOW_TRACKING_URI)
flask --app run benchmark --dry-run         # build the evidence and print the plan (no LLM calls)
flask --app run benchmark                   # 54 LLM calls; rewrites benchmark.md, benchmark_cases.csv, benchmark_reports.json
flask --app run benchmark --rescore         # recompute the metrics from the saved reports (no LLM calls)
```
