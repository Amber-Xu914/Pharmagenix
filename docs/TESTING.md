# Testing

How Pharmagenix is tested, and the expected and actual results of the key scenarios.

## How to run the tests

```bash
cd backend
.venv/bin/python -m pytest -q          # 152 tests, about 12 s
```

The automated tests need no network, no MySQL and no API keys:

- **Database:** an in-memory SQLite database is created and dropped for every test (`TestConfig` in `app/config.py`).
- **External sources:** DGIdb, VEP, openFDA and PubTator are replaced by fixed answers (`tests/conftest.py`,
  `tests/test_vcf.py`).
- **LLMs:** `FakeLLM` returns fixed text, so the citation checks run on known output.

Last run: 25 November 2026, **152 passed, 0 failed**.

The pipeline's accuracy on the 8 mock VCFs is measured separately, in [evaluation/README.md](evaluation/README.md)
and in the admin console's Evaluation tab (`flask --app run eval-metrics`).

## Key test scenarios

**Actual** is the observed result of the last run. Every automated scenario below passed. The rows marked *manual*
were checked in Chrome on the running app (`flask --app run run --port 5001`).

### Normal operation

| ID | Scenario | Input | Expected result | Actual | Test |
| --- | --- | --- | --- | --- | --- |
| N1 | Gene → drugs search | `{"gene": "CYP2C19"}` | 201; drug–gene pairs graded, a report whose citations all exist in the evidence | As expected | `test_api.py::test_explore_with_comparison` |
| N2 | Drug → genes search | `{"entity_type": "drug", "drug": "clopidogrel"}` | 201; genes for the drug, CPIC / FDA evidence | As expected | `test_api.py::test_explore_a_drug` |
| N3 | Patient VCF (EGFR L858R + T790M), logged in | mock VCF, LUAD | Osimertinib on the established track; gefitinib / erlotinib / afatinib down-ranked for resistance; QC artefacts removed | As expected | `test_vcf.py::test_qc_tiering_and_tracks` |
| N4 | Resistance to osimertinib | mock VCF with C797S | Osimertinib down-ranked | As expected | `test_vcf.py::test_t790m_with_c797s_down_ranks_osimertinib` |
| N5 | No approved targeted therapy | LUSC mock VCF | "No approved targeted therapy" outcome; exploratory track still ranked | As expected | `test_vcf.py::test_no_approved_targeted_therapy` |
| N6 | Current medications | gene search + `fluoxetine`, logged in | Interaction alerts from the 4 rules; phenoconversion note | As expected | `test_safety.py::test_phenoconversion`, `test_explore_passes_current_medications` |
| N7 | Patient genotype | `CYP2C19 *2/*2` | Poor Metabolizer → CPIC clopidogrel recommendation (Strong) | As expected | `test_genotypes.py::test_recommendations_match_the_phenotype_exactly` |
| N8 | Register, log in, log out | new account | Account created and logged in; password stored hashed; logout clears the session | As expected | `test_auth.py::test_register_logs_in`, `test_password_is_hashed`, `test_login_and_logout` |
| N9 | Rate a report | `{"rating": 4}` | 201; feedback row linked to the report and the user | As expected | `test_database.py::test_queries_and_feedback_belong_to_the_user` |
| N10 | Word report | completed search | `.docx` with Brief Results, methods, results tables and references | As expected | `test_report.py::test_gene_report`, `test_vcf.py::test_vcf_word_report` |
| N11 | Admin: prompt template | new template version | New version saved and activated; preview renders with a saved query | As expected | `test_admin.py::test_template_versioning_and_preview` |
| N12 | Complete workflow, end to end | register → upload VCF → reopen link → follow-up → rate twice → Word report → history → log out | Each step succeeds in order; one rating kept; history newest first; after logout 401, another account 403 | As expected | `test_end_to_end.py::test_complete_vcf_workflow` |

### Invalid input (server-side validation)

| ID | Scenario | Input | Expected result | Actual | Test |
| --- | --- | --- | --- | --- | --- |
| V1 | Empty / malformed search | `""`, a list, `"BRAF, EGFR"`, unknown relation type | 400 with a message; no API or LLM call made | As expected | `test_api.py::test_explore_validation` |
| V2 | Misspelt gene | `CYP2C91` | 422 "needs confirmation" with the suggestion CYP2C19; nothing saved | As expected | `test_workflow.py::test_unknown_gene_asks_for_confirmation` |
| V3 | Invalid registration | bad e-mail formats, 250-character e-mail, short password, duplicate e-mail | 400 for each invalid field; 409 for the duplicate (case-insensitive) | As expected | `test_auth.py::test_register_validation` |
| V4 | Wrong password / unknown account | login | 401 with the same message for both, so accounts cannot be enumerated | As expected | `test_auth.py::test_login_and_logout` |
| V5 | VCF upload without confirming test data | real-file upload, box unticked | 400: "accepts test data only" | As expected | `test_vcf.py::test_vcf_upload_needs_test_data_confirmation` |
| V6 | Unreadable variant file | text that is not VCF / MAF; MAF with 2 tumour samples | 400 with the reason | As expected | `test_vcf.py::test_parse_maf_and_errors` |
| V7 | Unreadable genotype | `CYP2C19` (no value); same gene twice | 400 "Could not read …" / "… twice" | As expected | `test_genotypes.py::test_parse_genotypes` |
| V8 | Invalid rating | rating 9 | 400 | As expected | `test_api.py::test_feedback` |

### Security and access control

| ID | Scenario | Input | Expected result | Actual | Test |
| --- | --- | --- | --- | --- | --- |
| S1 | Patient data without an account | VCF, medications or genotypes, logged out | 401 "Log in to …"; nothing run or saved | As expected | `test_access.py::test_patient_searches_need_login` |
| S2 | Another user's result | user B opens, asks, downloads or rates user A's VCF result | 403 "This result belongs to another account" | As expected | `test_access.py::test_owner_opens_own_result_and_others_get_403` |
| S3 | Shared link while logged out | `?q=<VCF result>` | 401; *manual:* the login panel opens and the result loads after logging in | As expected | `test_access.py::test_logged_out_visitor_gets_401_for_private_results` |
| S4 | Admin console | no token / wrong token | 401 | As expected | `test_admin.py::test_admin_requires_token` |
| S5 | Query history | logged out; ordinary user; admin | 401; own queries only; admin sees everyone's | As expected | `test_access.py::test_admin_opens_everything`, `test_database.py::test_history_lists_only_own_queries` |
| S6 | LLM cites evidence that does not exist | report citing `[E42]` | Report marked *flagged*, `E42` listed as invalid | As expected | `test_api.py::test_hallucinated_citation_is_flagged` |
| S7 | Logging out with a result on screen | *manual* | The page returns to the empty search form | As expected | manual, 24 Nov 2026 |
| S8 | Synthetic examples | logged out; ordinary user; admin | Logged out: 401. Ordinary user: no examples listed, running or downloading one gives 403, and *manual:* only the upload form is shown. Admin: all 8 examples and the *Synthetic example* tab | As expected | `test_access.py::test_synthetic_examples_are_for_admins_only` |

| S9 | Cross-site request forgery | POST without the CSRF token, with a guessed token, or a forged form POST carrying the session cookie | 403 "could not be verified"; nothing saved. The token changes at login and logout; a valid `X-Admin-Token` needs none | As expected | `test_security.py::test_state_changing_requests_need_the_csrf_token`, `test_forged_form_post_with_the_session_cookie_is_rejected`, `test_admin_token_header_needs_no_csrf_token` |
| S10 | Repeated submissions | the same rating sent twice; the same template saved twice | Rating updated, not duplicated (201 then 200); one template version | As expected | `test_security.py::test_resubmitted_feedback_updates_instead_of_duplicating`, `test_saving_the_same_template_twice_creates_one_version` |

SQL injection: all database access goes through SQLAlchemy, which sends values as bound parameters (prepared
statements); no SQL is built from user input.

### Failures of the database and external services

| ID | Scenario | Input | Expected result | Actual | Test |
| --- | --- | --- | --- | --- | --- |
| F1 | MySQL unavailable | the connection fails (`OperationalError`) | 503 "The database is unavailable at the moment …"; no stack trace or driver message shown | As expected | `test_errors.py::test_database_unavailable_gives_503` |
| F2 | A write fails half-way | the commit fails while saving feedback | 500 "A database error occurred and nothing was saved"; the transaction is rolled back (0 rows) | As expected | `test_errors.py::test_failed_write_is_rolled_back` |
| F3 | Unexpected server error | an exception in the workflow | 500 "Internal server error"; internals hidden | As expected | `test_errors.py::test_unexpected_error_hides_the_stack_trace` |
| F4 | Ensembl VEP down | VEP returns 503 | 503 naming Ensembl VEP, so the user knows which service failed | As expected | `test_vcf.py::test_vep_outage_is_a_clear_503` |
| F5 | External API down, earlier answer stored | the API returns 503 or refuses the connection | The stored answer is used and marked as replayed | As expected | `test_http_cache.py::test_fallback_serves_the_stored_answer_when_the_api_fails` |
| F6 | Reference data not imported | empty reference tables | Searches still run on the live APIs; the VCF workflow stops with the `import-data` command to run | As expected | `test_reference.py::test_collect_evidence_without_reference_data`, `test_vcf.py::test_pipeline_needs_reference_data` |

### Edge cases

| ID | Scenario | Input | Expected result | Actual | Test |
| --- | --- | --- | --- | --- | --- |
| E1 | No variant passes QC and tiering | VCF whose only variant is a common germline SNP | Outcome "none"; the QC audit gives the reason (gnomAD AF 4.5% > 1%); no LLM call | As expected | `test_vcf.py::test_nothing_tiered` |
| E2 | Only Tier 3 variants | `mock_luad_tier3_only` | No established drug; exploratory track still produced | As expected | `test_vcf.py::test_tier3_only_continues_downstream` |
| E3 | Search finds nothing | gene with no interactions | "No interaction" outcome; the LLM is not called | As expected | `test_workflow.py::test_llm_skipped_when_nothing_found` |
| E4 | Gene name that is also an alias / a drug | ambiguous alias; a drug typed as a gene | Asks which one was meant | As expected | `test_workflow.py::test_ambiguous_alias`, `test_drug_name_is_not_a_gene` |
| E5 | Regimen containing a resistant drug | erlotinib + ramucirumab with T790M | The regimen inherits the resistance | As expected | `test_vcf.py::test_regimen_inherits_resistance_of_its_drug` |
| E6 | Multi-gene CPIC recommendation, one gene missing | CYP2C19 only, for amitriptyline (also needs CYP2D6) | No recommendation; the missing gene is reported | As expected | `test_genotypes.py::test_recommendations_match_the_phenotype_exactly` |
| E7 | Old saved results with outdated links | patient-variant links to CIViC's retired search page | `upgrade-db` rewrites them; running it again changes nothing | As expected | `test_database.py::test_upgrade_db_fixes_old_civic_variant_links` |

## Manual checks of the web interface

Checked in Chrome (desktop 1280 px and iPhone 13 width) on 24 November 2026:

- **Result tabs:** Clinical, Extra and Export each show only their own sections.
- **Citations:** a citation in the Clinical tab (e.g. E9) switches to Extra and highlights the cited row.
- **Next step:** with no approved therapy, *At a glance* shows a *Next step* line linking to the exploratory track.
- **Errors:** messages appear in the status bar, and the login panel opens when the server answers 401.
- **Layout:** no horizontal scrolling on a phone; no JavaScript errors in the console.

Checked in Chrome on 25 November 2026:

- **CSRF:** logging in and out through the page works (the token is sent and renewed); a POST sent without the token
  gets 403 and changes nothing.
- **Variant tables:** the tiered-variant and QC audit tables show "Showing *n* of *m*" with a *Show more* button.
