import click
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateIndex, CreateTable

from .extensions import db
from .reference.importers import IMPORT_ORDER, run_import
from .reference.lookup import latest_releases
from .services.prompts import seed_default_template


def register_cli(app):
    @app.cli.command("init-db")
    @click.option("--drop", is_flag=True, help="Drop all tables first (destroys data).")
    def init_db(drop):
        """Create tables and seed the default prompt template."""
        if drop:
            db.drop_all()
        db.create_all()
        template = seed_default_template()
        click.echo(f"Database ready. Active template: {template.name} v{template.version}")

    @app.cli.command("import-data")
    @click.argument("sources", nargs=-1, type=click.Choice(IMPORT_ORDER + ["all"]))
    def import_data(sources):
        """Import reference datasets into the database: hgnc, chembl, cpic, pbs or all (default)."""
        selected = IMPORT_ORDER if not sources or "all" in sources else [s for s in IMPORT_ORDER if s in sources]
        for source in selected:
            run_import(source, echo=click.echo)

    @app.cli.command("reference-status")
    def reference_status():
        """Show which reference datasets are imported, and which versions."""
        releases = latest_releases()
        for source in IMPORT_ORDER:
            release = releases.get(source)
            if release:
                click.echo(f"{source:7} {release.version} · {release.record_counts} · imported {release.imported_at:%Y-%m-%d %H:%M}")
            else:
                click.echo(f"{source:7} not imported")

    @app.cli.command("dump-schema")
    def dump_schema():
        """Print MySQL DDL generated from the models (used to produce schema.sql)."""
        for table in db.metadata.sorted_tables:
            click.echo(str(CreateTable(table).compile(dialect=mysql.dialect())).strip() + ";\n")
            for index in sorted(table.indexes, key=lambda i: i.name):
                click.echo(str(CreateIndex(index).compile(dialect=mysql.dialect())).strip() + ";")
            click.echo()
