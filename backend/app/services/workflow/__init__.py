"""The gene → drug–gene interaction workflow, one module per stage:

  1. resolution.py  Entity Resolution: resolve the gene; stop and ask the user when it is unknown or ambiguous
  2. profiles.py    Gene Profile Retrieval: HGNC (local) and UniProt (live)
  3. (biomed/aggregator.py) DGI Retrieval: DGIdb, ChEMBL, CPIC, ClinPGx, DrugBank, PubTator and PBS
  4. evidence.py    Evidence: merge per gene–drug pair, grade, flag conflicts, decide "interactions found?"
  5. (llm/report_generator.py) Analysis: the LLM interprets the graded evidence
  6. Report: the frontend combines the LLM narrative with the rule-based table, profile and network

explorer.py runs the stages in order.
"""
