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
        """Import reference datasets (hgnc, chembl, cpic, clinpgx, dgidb, fda, oncotree, intogen, civic, hotspots, cbioportal, pbs) or all."""
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

    @app.cli.command("mlflow-ui")
    @click.option("--port", default=5050, show_default=True)
    def mlflow_ui(port):
        """Open the MLflow UI on the app's tracking database (MLFLOW_TRACKING_URI)."""
        import os
        import socket
        import subprocess
        import sys

        uri = app.config["MLFLOW_TRACKING_URI"]
        if not uri:
            raise click.ClickException("MLFLOW_TRACKING_URI is not set; add e.g. MLFLOW_TRACKING_URI=sqlite:///mlflow.db "
                                       "to backend/.env")
        url = f"http://127.0.0.1:{port}"
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                click.echo(f"Something is already running on port {port}: if it is the MLflow UI, open {url} "
                           f"(otherwise use --port).")
                return
        click.echo(f"MLflow UI on {uri}\nOpen {url}  (Ctrl+C to stop)")
        mlflow_bin = os.path.join(os.path.dirname(sys.executable), "mlflow")  # the venv's MLflow, not the PATH's
        subprocess.run([mlflow_bin, "ui", "--backend-store-uri", uri, "--port", str(port)], check=False)

    @app.cli.command("mlflow-eval")
    @click.option("--llm", is_flag=True, help="Run the whole workflow, including the LLM reports and agents (costs tokens).")
    @click.option("--case", "cases", multiple=True, help="Only this mock VCF (repeatable), e.g. mock_luad_kras_g12c.")
    def mlflow_eval(llm, cases):
        """Evaluate Workflow 4 on the mock VCFs against their expected answers (see app/evaluation.py)."""
        from .evaluation import run_evaluation

        try:
            result = run_evaluation(app, llm=llm, cases=cases)
        except (RuntimeError, ValueError) as exc:
            raise click.ClickException(str(exc))
        click.echo("\nPass rate per check:")
        for name, value in sorted(result.metrics.items()):
            click.echo(f"  {name:45} {value:.0%}" if isinstance(value, float) else f"  {name:45} {value}")
        click.echo(f"\nDetails: MLflow UI -> experiment {app.config['MLFLOW_EXPERIMENT_PREFIX']}-vcf -> Evaluation runs "
                   f"(run {result.run_id})")

    @app.cli.command("dump-schema")
    def dump_schema():
        """Print MySQL DDL generated from the models (used to produce schema.sql)."""
        for table in db.metadata.sorted_tables:
            click.echo(str(CreateTable(table).compile(dialect=mysql.dialect())).strip() + ";\n")
            for index in sorted(table.indexes, key=lambda i: i.name):
                click.echo(str(CreateIndex(index).compile(dialect=mysql.dialect())).strip() + ";")
            click.echo()
