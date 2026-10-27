"""In-place upgrade of an existing database to the current models (flask --app run upgrade-db). Idempotent.

db.create_all() only creates missing tables, so the columns added to existing tables, and the data moved out of
JSON columns into their own tables, are handled here:

  queries.user_id, feedback.user_id    link searches and ratings to the account that made them
  users.is_admin                       admin accounts can open the admin console without the shared token
  reports.citations / invalid_citations (JSON)  ->  citations table, then the JSON columns are dropped
  queries.evidence drug–gene pairs     ->  query_interactions (for queries made before the table existed)
  gene_pathways                        seeded from services/variants/biology.py
"""

import json

from sqlalchemy import inspect, text

from .extensions import db
from .models import Citation, Query
from .services.interactions import record_interactions
from .services.prompts import seed_default_template
from .services.variants.biology import seed_pathways

NEW_COLUMNS = [  # (table, column, DDL type, foreign key target or None)
    ("queries", "user_id", "INTEGER NULL", "users(id)"),
    ("feedback", "user_id", "INTEGER NULL", "users(id)"),
    ("users", "is_admin", "BOOLEAN NOT NULL DEFAULT 0", None),
]


def upgrade(echo=print):
    db.create_all()  # new tables: citations, query_interactions, gene_pathways, ...
    engine = db.engine
    mysql = engine.dialect.name == "mysql"
    insp = inspect(engine)
    with engine.begin() as conn:
        for table, column, ddl, target in NEW_COLUMNS:
            if column in {c["name"] for c in insp.get_columns(table)}:
                continue
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
            if target:
                conn.execute(text(f"CREATE INDEX ix_{table}_{column} ON {table} ({column})"))
                if mysql:  # SQLite can't add a foreign key to an existing table
                    conn.execute(text(f"ALTER TABLE {table} ADD CONSTRAINT fk_{table}_{column} FOREIGN KEY ({column}) "
                                      f"REFERENCES {target} ON DELETE SET NULL"))
            echo(f"added {table}.{column}")

    moved = _move_citations(insp)
    if moved is not None:
        echo(f"moved {moved} citations from reports JSON into the citations table; dropped the JSON columns")

    added = 0
    for query in db.session.scalars(db.select(Query).where(Query.evidence.is_not(None), ~Query.interactions.any())):
        record_interactions(query)  # queries whose evidence has no drug–gene pair stay without rows
        added += len(query.interactions)
    db.session.commit()
    echo(f"query_interactions: {added} rows added for earlier queries")
    echo(f"gene_pathways: {seed_pathways()} rows")
    template = seed_default_template()
    echo(f"Database up to date. Active template: {template.name} v{template.version}")


def _move_citations(insp):
    """None when the old JSON columns are already gone."""
    if "citations" not in {c["name"] for c in insp.get_columns("reports")}:
        return None
    rows = db.session.execute(text("SELECT r.id, r.citations, r.invalid_citations, q.evidence FROM reports r "
                                   "JOIN queries q ON q.id = r.query_id")).all()
    have = set(db.session.scalars(db.select(Citation.report_id).distinct()))
    moved = 0
    for report_id, valid, invalid, evidence in rows:
        if report_id in have:
            continue
        valid, invalid, evidence = (_json(valid) or []), (_json(invalid) or []), (_json(evidence) or {})
        items = {i["id"]: i for i in evidence.get("items") or []}
        for position, eid in enumerate(valid + [e for e in invalid if e not in valid], 1):
            citation = Citation.from_item(position, eid, eid not in invalid, items.get(eid))
            citation.report_id = report_id
            db.session.add(citation)
            moved += 1
    db.session.commit()
    with db.engine.begin() as conn:
        for column in ("citations", "invalid_citations"):
            conn.execute(text(f"ALTER TABLE reports DROP COLUMN {column}"))
    return moved


def _json(value):
    return json.loads(value) if isinstance(value, str) else value
