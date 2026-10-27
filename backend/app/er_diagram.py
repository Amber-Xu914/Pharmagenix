"""Mermaid ER diagrams generated from the SQLAlchemy models (flask --app run dump-er), so docs/database.md never
drifts from the schema. Two diagrams: the application tables, and the reference datasets."""

from .extensions import db

APP_TABLES = ["users", "queries", "reports", "citations", "prompt_logs", "prompt_templates", "feedback",
              "query_interactions"]
# Logical references without a declared foreign key (reference tables are replaced wholesale on import).
LOGICAL = [("query_interactions", "genes", "hgnc_id"), ("query_interactions", "drugs", "chembl_id"),
           ("drug_mechanisms", "genes", "hgnc_id"), ("gene_pathways", "genes", "gene_symbol"),
           ("pbs_item_restrictions", "pbs_restrictions", "res_code"), ("pbs_item_restrictions", "pbs_items", "pbs_code"),
           ("civic_evidence", "genes", "gene_symbol"), ("cpic_pairs", "drugs", "chembl_id")]


def _type(column):
    name = type(column.type).__name__.lower()
    return {"string": "varchar", "text": "text", "integer": "int", "smallinteger": "smallint", "float": "float",
            "boolean": "bool", "datetime": "datetime", "date": "date", "json": "json"}.get(name, name)


def _entity(table):
    lines = [f"    {table.name} {{"]
    for c in table.columns:
        keys = ",".join(k for k, on in (("PK", c.primary_key), ("FK", bool(c.foreign_keys))) if on)
        lines.append(f"        {_type(c)} {c.name}{' ' + keys if keys else ''}")
    return lines + ["    }"]


def _diagram(tables, title):
    names = {t.name for t in tables}
    lines = [f"%% {title}", "erDiagram"]
    for table in tables:
        for fk in table.foreign_keys:
            parent = fk.column.table.name
            if parent in names and parent != table.name:
                lines.append(f'    {parent} ||--o{{ {table.name} : "{fk.parent.name}"')
            elif parent == table.name:
                lines.append(f'    {parent} |o--o{{ {table.name} : "{fk.parent.name} (follow-up of)"')
    for child, parent, column in LOGICAL:
        if child in names and parent in names:
            lines.append(f'    {parent} ||..o{{ {child} : "{column} (logical)"')
    for table in tables:
        lines += _entity(table)
    return "\n".join(lines)


def mermaid():
    tables = db.metadata.sorted_tables
    app_tables = [t for t in tables if t.name in APP_TABLES]
    reference = [t for t in tables if t.name not in APP_TABLES]
    # the reference diagram shows genes and drugs once more so the logical links have something to point at
    return (_diagram(app_tables + [t for t in tables if t.name in ("genes", "drugs")], "Application tables") + "\n\n"
            + _diagram(reference, "Reference datasets"))
