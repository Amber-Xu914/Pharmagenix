-- Generated from the SQLAlchemy models via: flask --app run dump-schema
-- Normally you don't need this: 'flask --app run init-db' creates the tables.
CREATE DATABASE IF NOT EXISTS dgi_explorer CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
USE dgi_explorer;

CREATE TABLE cancer_hotspots (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	gene_symbol VARCHAR(64) NOT NULL, 
	residue VARCHAR(32) NOT NULL, 
	start INTEGER NOT NULL, 
	end INTEGER NOT NULL, 
	hotspot_type VARCHAR(32), 
	tumor_count INTEGER, 
	variant_amino_acids JSON, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_cancer_hotspots_gene_symbol ON cancer_hotspots (gene_symbol);

CREATE TABLE civic_evidence (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	evidence_id INTEGER NOT NULL, 
	gene_symbol VARCHAR(64) NOT NULL, 
	molecular_profile VARCHAR(500) NOT NULL, 
	molecular_profile_id INTEGER, 
	components JSON, 
	disease VARCHAR(255), 
	doid VARCHAR(20), 
	therapies JSON, 
	therapy_interaction VARCHAR(32), 
	direction VARCHAR(16), 
	level VARCHAR(1), 
	significance VARCHAR(32), 
	statement TEXT, 
	pmid INTEGER, 
	citation VARCHAR(255), 
	rating INTEGER, 
	variant_origin VARCHAR(32), 
	url VARCHAR(255), 
	PRIMARY KEY (id)
);

CREATE INDEX ix_civic_evidence_doid ON civic_evidence (doid);
CREATE INDEX ix_civic_evidence_evidence_id ON civic_evidence (evidence_id);
CREATE INDEX ix_civic_evidence_gene_symbol ON civic_evidence (gene_symbol);

CREATE TABLE clinpgx_drug_labels (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	label_id VARCHAR(20) NOT NULL, 
	name VARCHAR(500), 
	source VARCHAR(32), 
	gene_symbol VARCHAR(64), 
	hgnc_id VARCHAR(20), 
	drug_name VARCHAR(255), 
	drug_name_norm VARCHAR(255), 
	chembl_id VARCHAR(20), 
	biomarker_flag VARCHAR(32), 
	testing_level VARCHAR(64), 
	has_prescribing_info BOOL, 
	has_dosing_info BOOL, 
	has_alternate_drug BOOL, 
	has_other_prescribing_guidance BOOL, 
	cancer_genome BOOL, 
	variants TEXT, 
	latest_history_date DATE, 
	url VARCHAR(255), 
	PRIMARY KEY (id)
);

CREATE INDEX ix_clinpgx_drug_labels_chembl_id ON clinpgx_drug_labels (chembl_id);
CREATE INDEX ix_clinpgx_drug_labels_drug_name_norm ON clinpgx_drug_labels (drug_name_norm);
CREATE INDEX ix_clinpgx_drug_labels_gene_symbol ON clinpgx_drug_labels (gene_symbol);
CREATE INDEX ix_clinpgx_drug_labels_label_id ON clinpgx_drug_labels (label_id);

CREATE TABLE clinpgx_guidelines (
	guideline_id VARCHAR(20) NOT NULL, 
	name VARCHAR(500) NOT NULL, 
	source VARCHAR(32), 
	recommendation BOOL, 
	dosing_information BOOL, 
	alternate_drug_available BOOL, 
	other_prescribing_guidance BOOL, 
	has_testing_info BOOL, 
	pediatric BOOL, 
	cancer_genome BOOL, 
	summary TEXT, 
	text LONGTEXT, 
	pmids JSON, 
	url VARCHAR(255), 
	PRIMARY KEY (guideline_id)
);

CREATE INDEX ix_clinpgx_guidelines_source ON clinpgx_guidelines (source);

CREATE TABLE clinpgx_relationships (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	gene_pa_id VARCHAR(20) NOT NULL, 
	gene_symbol VARCHAR(64) NOT NULL, 
	hgnc_id VARCHAR(20), 
	chemical_pa_id VARCHAR(20) NOT NULL, 
	drug_name VARCHAR(255) NOT NULL, 
	drug_name_norm VARCHAR(255) NOT NULL, 
	chembl_id VARCHAR(20), 
	evidence JSON, 
	association VARCHAR(32), 
	pk BOOL, 
	pd BOOL, 
	pmids JSON, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_clinpgx_relationships_chembl_id ON clinpgx_relationships (chembl_id);
CREATE INDEX ix_clinpgx_relationships_drug_name_norm ON clinpgx_relationships (drug_name_norm);
CREATE INDEX ix_clinpgx_relationships_gene_symbol ON clinpgx_relationships (gene_symbol);

CREATE TABLE cpic_allele_frequencies (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	gene_symbol VARCHAR(64) NOT NULL, 
	allele_name VARCHAR(128) NOT NULL, 
	population_group VARCHAR(64) NOT NULL, 
	subject_count INTEGER, 
	freq_weighted_avg FLOAT, 
	freq_avg FLOAT, 
	freq_min FLOAT, 
	freq_max FLOAT, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_cpic_allele_frequencies_gene_symbol ON cpic_allele_frequencies (gene_symbol);

CREATE TABLE cpic_alleles (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	gene_symbol VARCHAR(64) NOT NULL, 
	allele_name VARCHAR(128) NOT NULL, 
	function_status VARCHAR(128), 
	clinical_function_status VARCHAR(128), 
	clinical_function_substrate VARCHAR(128), 
	activity_value VARCHAR(16), 
	strength VARCHAR(32), 
	pmids JSON, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_cpic_alleles_gene_symbol ON cpic_alleles (gene_symbol);

CREATE TABLE cpic_diplotypes (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	gene_symbol VARCHAR(64) NOT NULL, 
	diplotype VARCHAR(160) NOT NULL, 
	function1 VARCHAR(128), 
	function2 VARCHAR(128), 
	activity_value1 VARCHAR(16), 
	activity_value2 VARCHAR(16), 
	total_activity_score VARCHAR(16), 
	phenotype VARCHAR(128), 
	lookup_key JSON, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_cpic_diplotypes_gene_symbol ON cpic_diplotypes (gene_symbol);

CREATE TABLE cpic_gene_results (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	gene_symbol VARCHAR(64) NOT NULL, 
	result VARCHAR(128) NOT NULL, 
	activity_score VARCHAR(16), 
	ehr_priority VARCHAR(64), 
	consultation_text TEXT, 
	frequency JSON, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_cpic_gene_results_gene_symbol ON cpic_gene_results (gene_symbol);

CREATE TABLE cpic_pairs (
	pair_id INTEGER NOT NULL AUTO_INCREMENT, 
	gene_symbol VARCHAR(64) NOT NULL, 
	hgnc_id VARCHAR(20), 
	drug_rxnorm_id VARCHAR(32), 
	drug_name VARCHAR(255) NOT NULL, 
	drug_name_norm VARCHAR(255) NOT NULL, 
	chembl_id VARCHAR(20), 
	guideline_id INTEGER, 
	guideline_name VARCHAR(255), 
	guideline_url VARCHAR(500), 
	cpic_level VARCHAR(8), 
	clinpgx_level VARCHAR(8), 
	pgx_testing VARCHAR(64), 
	used_for_recommendation BOOL, 
	pmids JSON, 
	PRIMARY KEY (pair_id)
);

CREATE INDEX ix_cpic_pairs_chembl_id ON cpic_pairs (chembl_id);
CREATE INDEX ix_cpic_pairs_drug_name_norm ON cpic_pairs (drug_name_norm);
CREATE INDEX ix_cpic_pairs_gene_symbol ON cpic_pairs (gene_symbol);
CREATE INDEX ix_cpic_pairs_guideline_id ON cpic_pairs (guideline_id);
CREATE INDEX ix_cpic_pairs_hgnc_id ON cpic_pairs (hgnc_id);

CREATE TABLE cpic_recommendations (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	guideline_id INTEGER, 
	drug_name VARCHAR(255), 
	drug_name_norm VARCHAR(255), 
	population VARCHAR(128), 
	lookup JSON, 
	implications JSON, 
	recommendation LONGTEXT, 
	classification VARCHAR(32), 
	comments TEXT, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_cpic_recommendations_drug_name_norm ON cpic_recommendations (drug_name_norm);
CREATE INDEX ix_cpic_recommendations_guideline_id ON cpic_recommendations (guideline_id);

CREATE TABLE cpic_test_alerts (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	drug_rxnorm_id VARCHAR(32), 
	drug_name VARCHAR(255), 
	drug_name_norm VARCHAR(255), 
	cds_context VARCHAR(16), 
	population VARCHAR(128), 
	genes JSON, 
	lookup_key JSON, 
	alert_text JSON, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_cpic_test_alerts_drug_name_norm ON cpic_test_alerts (drug_name_norm);

CREATE TABLE dgidb_gene_categories (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	gene_symbol VARCHAR(64) NOT NULL, 
	hgnc_id VARCHAR(20), 
	gene_claim_name VARCHAR(255), 
	category VARCHAR(128) NOT NULL, 
	source_db VARCHAR(64), 
	PRIMARY KEY (id)
);

CREATE INDEX ix_dgidb_gene_categories_gene_symbol ON dgidb_gene_categories (gene_symbol);

CREATE TABLE drug_cyp_roles (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	drug_name VARCHAR(255) NOT NULL, 
	drug_name_norm VARCHAR(255) NOT NULL, 
	chembl_id VARCHAR(20), 
	`system` VARCHAR(16) NOT NULL, 
	pathway VARCHAR(32) NOT NULL, 
	gene_symbol VARCHAR(64), 
	hgnc_id VARCHAR(20), 
	`role` VARCHAR(16) NOT NULL, 
	strength VARCHAR(32), 
	notes TEXT, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_drug_cyp_roles_chembl_id ON drug_cyp_roles (chembl_id);
CREATE INDEX ix_drug_cyp_roles_drug_name_norm ON drug_cyp_roles (drug_name_norm);
CREATE INDEX ix_drug_cyp_roles_gene_symbol ON drug_cyp_roles (gene_symbol);

CREATE TABLE drug_mechanisms (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	mec_id INTEGER NOT NULL, 
	chembl_id VARCHAR(20) NOT NULL, 
	drug_name VARCHAR(255), 
	target_chembl_id VARCHAR(20) NOT NULL, 
	target_name VARCHAR(255), 
	target_type VARCHAR(64), 
	hgnc_id VARCHAR(20), 
	gene_symbol VARCHAR(64) NOT NULL, 
	action_type VARCHAR(64), 
	mechanism_of_action VARCHAR(500), 
	direct_interaction BOOL, 
	disease_efficacy BOOL, 
	max_phase FLOAT, 
	pmids JSON, 
	ref_urls JSON, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_drug_mechanisms_chembl_id ON drug_mechanisms (chembl_id);
CREATE INDEX ix_drug_mechanisms_gene_symbol ON drug_mechanisms (gene_symbol);
CREATE INDEX ix_drug_mechanisms_hgnc_id ON drug_mechanisms (hgnc_id);
CREATE INDEX ix_drug_mechanisms_mec_id ON drug_mechanisms (mec_id);

CREATE TABLE drugs (
	chembl_id VARCHAR(20) NOT NULL, 
	pref_name VARCHAR(255), 
	molecule_type VARCHAR(64), 
	max_phase FLOAT, 
	first_approval INTEGER, 
	PRIMARY KEY (chembl_id)
);

CREATE INDEX ix_drugs_pref_name ON drugs (pref_name);

CREATE TABLE fda_pgx_biomarkers (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	`row_number` INTEGER NOT NULL, 
	drug_label VARCHAR(255) NOT NULL, 
	drug_name VARCHAR(255) NOT NULL, 
	drug_name_norm VARCHAR(255) NOT NULL, 
	chembl_id VARCHAR(20), 
	therapeutic_area VARCHAR(128), 
	biomarker VARCHAR(255) NOT NULL, 
	gene_symbol VARCHAR(64), 
	hgnc_id VARCHAR(20), 
	labeling_sections JSON, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_fda_pgx_biomarkers_chembl_id ON fda_pgx_biomarkers (chembl_id);
CREATE INDEX ix_fda_pgx_biomarkers_drug_name_norm ON fda_pgx_biomarkers (drug_name_norm);
CREATE INDEX ix_fda_pgx_biomarkers_gene_symbol ON fda_pgx_biomarkers (gene_symbol);

CREATE TABLE gene_pathways (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	pathway VARCHAR(64) NOT NULL, 
	gene_symbol VARCHAR(64) NOT NULL, 
	source VARCHAR(255) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_gene_pathway UNIQUE (pathway, gene_symbol)
);

CREATE INDEX ix_gene_pathways_gene_symbol ON gene_pathways (gene_symbol);
CREATE INDEX ix_gene_pathways_pathway ON gene_pathways (pathway);

CREATE TABLE genes (
	hgnc_id VARCHAR(20) NOT NULL, 
	symbol VARCHAR(64) NOT NULL, 
	name VARCHAR(255), 
	locus_group VARCHAR(64), 
	locus_type VARCHAR(64), 
	location VARCHAR(64), 
	entrez_id VARCHAR(20), 
	ensembl_gene_id VARCHAR(32), 
	uniprot_ids JSON, 
	PRIMARY KEY (hgnc_id)
);

CREATE INDEX ix_genes_ensembl_gene_id ON genes (ensembl_gene_id);
CREATE INDEX ix_genes_entrez_id ON genes (entrez_id);
CREATE INDEX ix_genes_symbol ON genes (symbol);

CREATE TABLE intogen_drivers (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	gene_symbol VARCHAR(64) NOT NULL, 
	cancer_type VARCHAR(32) NOT NULL, 
	cohort VARCHAR(100) NOT NULL, 
	`role` VARCHAR(16), 
	samples INTEGER, 
	pct_samples FLOAT, 
	qvalue FLOAT, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_intogen_drivers_cancer_type ON intogen_drivers (cancer_type);
CREATE INDEX ix_intogen_drivers_gene_symbol ON intogen_drivers (gene_symbol);

CREATE TABLE oncotree_types (
	code VARCHAR(20) NOT NULL, 
	name VARCHAR(255) NOT NULL, 
	main_type VARCHAR(255), 
	tissue VARCHAR(100), 
	parent VARCHAR(20), 
	level INTEGER, 
	PRIMARY KEY (code)
);

CREATE INDEX ix_oncotree_types_tissue ON oncotree_types (tissue);

CREATE TABLE pbs_item_restrictions (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	pbs_code VARCHAR(16) NOT NULL, 
	res_code VARCHAR(32) NOT NULL, 
	benefit_type_code VARCHAR(4), 
	PRIMARY KEY (id)
);

CREATE INDEX ix_pbs_item_restrictions_pbs_code ON pbs_item_restrictions (pbs_code);
CREATE INDEX ix_pbs_item_restrictions_res_code ON pbs_item_restrictions (res_code);

CREATE TABLE pbs_items (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	pbs_code VARCHAR(16) NOT NULL, 
	drug_name VARCHAR(255) NOT NULL, 
	drug_name_norm VARCHAR(255) NOT NULL, 
	chembl_id VARCHAR(20), 
	brand_name VARCHAR(255), 
	brand_name_norm VARCHAR(255), 
	form VARCHAR(255), 
	schedule_form VARCHAR(500), 
	program_code VARCHAR(8), 
	benefit_type_code VARCHAR(4), 
	route VARCHAR(64), 
	schedule_code INTEGER, 
	effective_date DATE, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_pbs_items_brand_name_norm ON pbs_items (brand_name_norm);
CREATE INDEX ix_pbs_items_chembl_id ON pbs_items (chembl_id);
CREATE INDEX ix_pbs_items_drug_name_norm ON pbs_items (drug_name_norm);
CREATE INDEX ix_pbs_items_pbs_code ON pbs_items (pbs_code);

CREATE TABLE pbs_restrictions (
	res_code VARCHAR(32) NOT NULL, 
	treatment_phase VARCHAR(255), 
	authority_method VARCHAR(64), 
	html_text LONGTEXT, 
	schedule_code INTEGER, 
	PRIMARY KEY (res_code)
);


CREATE TABLE prompt_templates (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	name VARCHAR(100) NOT NULL, 
	version INTEGER NOT NULL, 
	system_prompt LONGTEXT NOT NULL, 
	user_template LONGTEXT NOT NULL, 
	notes TEXT, 
	is_active BOOL NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_template_name_version UNIQUE (name, version)
);

CREATE INDEX ix_prompt_templates_is_active ON prompt_templates (is_active);

CREATE TABLE source_releases (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	source VARCHAR(32) NOT NULL, 
	version VARCHAR(100), 
	url VARCHAR(500), 
	record_counts JSON, 
	status VARCHAR(16) NOT NULL, 
	notes TEXT, 
	imported_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_source_releases_source ON source_releases (source);

CREATE TABLE subtype_gene_frequencies (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	oncotree_code VARCHAR(20) NOT NULL, 
	study_id VARCHAR(100) NOT NULL, 
	gene_symbol VARCHAR(64) NOT NULL, 
	altered INTEGER NOT NULL, 
	profiled INTEGER NOT NULL, 
	frequency FLOAT NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_subtype_gene_frequencies_gene_symbol ON subtype_gene_frequencies (gene_symbol);
CREATE INDEX ix_subtype_gene_frequencies_oncotree_code ON subtype_gene_frequencies (oncotree_code);

CREATE TABLE users (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	email VARCHAR(254) NOT NULL, 
	password_hash VARCHAR(255) NOT NULL, 
	is_admin BOOL NOT NULL DEFAULT false, 
	created_at DATETIME NOT NULL, 
	last_login_at DATETIME, 
	PRIMARY KEY (id), 
	UNIQUE (email)
);


CREATE TABLE clinpgx_guideline_pairs (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	guideline_id VARCHAR(20) NOT NULL, 
	gene_symbol VARCHAR(64), 
	hgnc_id VARCHAR(20), 
	drug_name VARCHAR(255), 
	drug_name_norm VARCHAR(255), 
	chembl_id VARCHAR(20), 
	PRIMARY KEY (id), 
	FOREIGN KEY(guideline_id) REFERENCES clinpgx_guidelines (guideline_id) ON DELETE CASCADE
);

CREATE INDEX ix_clinpgx_guideline_pairs_chembl_id ON clinpgx_guideline_pairs (chembl_id);
CREATE INDEX ix_clinpgx_guideline_pairs_drug_name_norm ON clinpgx_guideline_pairs (drug_name_norm);
CREATE INDEX ix_clinpgx_guideline_pairs_gene_symbol ON clinpgx_guideline_pairs (gene_symbol);
CREATE INDEX ix_clinpgx_guideline_pairs_guideline_id ON clinpgx_guideline_pairs (guideline_id);

CREATE TABLE drug_synonyms (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	chembl_id VARCHAR(20) NOT NULL, 
	synonym VARCHAR(255) NOT NULL, 
	synonym_norm VARCHAR(255) NOT NULL, 
	syn_type VARCHAR(32) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(chembl_id) REFERENCES drugs (chembl_id) ON DELETE CASCADE
);

CREATE INDEX ix_drug_synonyms_chembl_id ON drug_synonyms (chembl_id);
CREATE INDEX ix_drug_synonyms_synonym_norm ON drug_synonyms (synonym_norm);

CREATE TABLE drug_warnings (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	warning_id INTEGER NOT NULL, 
	chembl_id VARCHAR(20) NOT NULL, 
	warning_type VARCHAR(32) NOT NULL, 
	warning_class VARCHAR(128), 
	description TEXT, 
	country VARCHAR(500), 
	year INTEGER, 
	refs JSON, 
	PRIMARY KEY (id), 
	FOREIGN KEY(chembl_id) REFERENCES drugs (chembl_id) ON DELETE CASCADE
);

CREATE INDEX ix_drug_warnings_chembl_id ON drug_warnings (chembl_id);
CREATE INDEX ix_drug_warnings_warning_type ON drug_warnings (warning_type);

CREATE TABLE gene_aliases (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	hgnc_id VARCHAR(20) NOT NULL, 
	alias VARCHAR(64) NOT NULL, 
	alias_norm VARCHAR(64) NOT NULL, 
	alias_type VARCHAR(16) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(hgnc_id) REFERENCES genes (hgnc_id) ON DELETE CASCADE
);

CREATE INDEX ix_gene_aliases_alias_norm ON gene_aliases (alias_norm);
CREATE INDEX ix_gene_aliases_hgnc_id ON gene_aliases (hgnc_id);

CREATE TABLE queries (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	parent_id INTEGER, 
	user_id INTEGER, 
	entities JSON NOT NULL, 
	entity_type VARCHAR(16) NOT NULL, 
	relation_types JSON NOT NULL, 
	followup_question TEXT, 
	evidence JSON, 
	status VARCHAR(16) NOT NULL, 
	error_message TEXT, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(parent_id) REFERENCES queries (id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_queries_created_at ON queries (created_at);
CREATE INDEX ix_queries_parent_id ON queries (parent_id);
CREATE INDEX ix_queries_user_id ON queries (user_id);

CREATE TABLE query_drug_interactions (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	query_id INTEGER NOT NULL, 
	evidence_id VARCHAR(16) NOT NULL, 
	drug_name VARCHAR(255) NOT NULL, 
	chembl_id VARCHAR(20), 
	other_drug_name VARCHAR(255) NOT NULL, 
	other_chembl_id VARCHAR(20), 
	context VARCHAR(32) NOT NULL, 
	severity VARCHAR(16) NOT NULL, 
	mechanism VARCHAR(255), 
	rule VARCHAR(32) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(query_id) REFERENCES queries (id) ON DELETE CASCADE
);

CREATE INDEX ix_query_drug_interactions_chembl_id ON query_drug_interactions (chembl_id);
CREATE INDEX ix_query_drug_interactions_drug_name ON query_drug_interactions (drug_name);
CREATE INDEX ix_query_drug_interactions_other_chembl_id ON query_drug_interactions (other_chembl_id);
CREATE INDEX ix_query_drug_interactions_other_drug_name ON query_drug_interactions (other_drug_name);
CREATE INDEX ix_query_drug_interactions_query_id ON query_drug_interactions (query_id);

CREATE TABLE query_interactions (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	query_id INTEGER NOT NULL, 
	evidence_id VARCHAR(16) NOT NULL, 
	source VARCHAR(32) NOT NULL, 
	gene_symbol VARCHAR(64) NOT NULL, 
	hgnc_id VARCHAR(20), 
	drug_name VARCHAR(255) NOT NULL, 
	chembl_id VARCHAR(20), 
	relation VARCHAR(255), 
	level VARCHAR(16), 
	score FLOAT, 
	PRIMARY KEY (id), 
	FOREIGN KEY(query_id) REFERENCES queries (id) ON DELETE CASCADE
);

CREATE INDEX ix_query_interactions_chembl_id ON query_interactions (chembl_id);
CREATE INDEX ix_query_interactions_drug_name ON query_interactions (drug_name);
CREATE INDEX ix_query_interactions_gene_symbol ON query_interactions (gene_symbol);
CREATE INDEX ix_query_interactions_hgnc_id ON query_interactions (hgnc_id);
CREATE INDEX ix_query_interactions_query_id ON query_interactions (query_id);

CREATE TABLE reports (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	query_id INTEGER NOT NULL, 
	template_id INTEGER, 
	provider VARCHAR(32) NOT NULL, 
	model VARCHAR(100) NOT NULL, 
	`role` VARCHAR(16) NOT NULL, 
	content_md LONGTEXT, 
	status VARCHAR(16) NOT NULL, 
	error_message TEXT, 
	latency_ms INTEGER, 
	input_tokens INTEGER, 
	output_tokens INTEGER, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(query_id) REFERENCES queries (id), 
	FOREIGN KEY(template_id) REFERENCES prompt_templates (id)
);

CREATE INDEX ix_reports_created_at ON reports (created_at);
CREATE INDEX ix_reports_query_id ON reports (query_id);
CREATE INDEX ix_reports_status ON reports (status);

CREATE TABLE citations (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	report_id INTEGER NOT NULL, 
	position SMALLINT NOT NULL, 
	evidence_id VARCHAR(16) NOT NULL, 
	valid BOOL NOT NULL, 
	source VARCHAR(32), 
	kind VARCHAR(32), 
	pmid INTEGER, 
	url VARCHAR(500), 
	PRIMARY KEY (id), 
	CONSTRAINT uq_citation_report_evidence UNIQUE (report_id, evidence_id), 
	FOREIGN KEY(report_id) REFERENCES reports (id) ON DELETE CASCADE
);

CREATE INDEX ix_citations_pmid ON citations (pmid);
CREATE INDEX ix_citations_report_id ON citations (report_id);

CREATE TABLE feedback (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	report_id INTEGER NOT NULL, 
	user_id INTEGER, 
	rating SMALLINT, 
	comment TEXT, 
	suggestion TEXT, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(report_id) REFERENCES reports (id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_feedback_report_id ON feedback (report_id);
CREATE INDEX ix_feedback_user_id ON feedback (user_id);

CREATE TABLE prompt_logs (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	report_id INTEGER, 
	query_id INTEGER, 
	template_id INTEGER, 
	provider VARCHAR(32) NOT NULL, 
	model VARCHAR(100) NOT NULL, 
	system_prompt LONGTEXT, 
	user_prompt LONGTEXT, 
	raw_response LONGTEXT, 
	status VARCHAR(16) NOT NULL, 
	error_message TEXT, 
	latency_ms INTEGER, 
	input_tokens INTEGER, 
	output_tokens INTEGER, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(report_id) REFERENCES reports (id), 
	FOREIGN KEY(query_id) REFERENCES queries (id), 
	FOREIGN KEY(template_id) REFERENCES prompt_templates (id)
);

CREATE INDEX ix_prompt_logs_created_at ON prompt_logs (created_at);
CREATE INDEX ix_prompt_logs_provider ON prompt_logs (provider);
CREATE INDEX ix_prompt_logs_query_id ON prompt_logs (query_id);
CREATE INDEX ix_prompt_logs_report_id ON prompt_logs (report_id);
CREATE INDEX ix_prompt_logs_status ON prompt_logs (status);

