"""Workflow 4: patient VCF + cancer type -> candidate drugs (lung cancer prototype, synthetic test data only)."""

from flask import Blueprint, current_app, jsonify, request, send_file

from ..accounts import current_user, require_login
from ..errors import APIError
from ..reference.lookup import latest_releases
from ..services.vcf_explorer import load_example, mock_examples, mock_path, read_upload, run_vcf_exploration
from ..services.safety import parse_medications
from ..services.variants.clinical import parse_clinical
from ..services.variants import alphagenome
from ..services.variants.cancer_types import lung_types
from .explore import _query_response

bp = Blueprint("vcf", __name__)


def _is_admin():
    user = current_user()
    return bool(user and user.is_admin)


def require_admin_for_examples():
    """The synthetic examples are for administrators (demos, evaluation); other users upload their own VCF."""
    require_login("Log in to use the synthetic examples")
    if not _is_admin():
        raise APIError("The synthetic examples are available to administrators only; please upload a VCF", 403)


@bp.get("/options")
def options():
    """What the VCF form needs: lung cancer types, the QC thresholds and (administrators only) the synthetic mock
    VCFs."""
    cfg = current_app.config
    releases = latest_releases()
    return jsonify(
        cancer_types=lung_types(),
        examples=mock_examples() if _is_admin() else [],
        oncokb_enabled=bool(cfg["ONCOKB_API_TOKEN"]),
        ready=all(s in releases for s in ("civic", "intogen", "hotspots")),
        max_bytes=cfg["MAX_VCF_BYTES"],
        qc={"min_depth": cfg["VCF_QC_MIN_DEPTH"], "min_alt_reads": cfg["VCF_QC_MIN_ALT_READS"],
            "min_vaf": cfg["VCF_QC_MIN_VAF"], "max_population_af": cfg["VCF_QC_MAX_POPULATION_AF"]},
        ism=alphagenome.settings(),
    )


@bp.get("/examples/<example_id>")
def download_example(example_id):
    """The mock VCF itself, so administrators can see what the test data looks like."""
    require_admin_for_examples()
    example, path = mock_path(example_id)
    return send_file(path, mimetype="text/plain", as_attachment=True, download_name=example["file"])


@bp.post("/explore")
def explore():
    """Multipart upload (file, cancer_type, test_data, compare, medications) or JSON {example, cancer_type, compare,
    medications, clinical}. medications: the patient's current medications (list, or comma-separated), checked for
    drug–drug interactions with the candidates. clinical: optional context (stage, treatment, age, sex, smoking,
    purity, ecog, brain_mets, notes; form fields "clinical_<key>"), shown in the report and given to the agents.
    Needs a logged-in account: the result is private to it."""
    require_login("Log in to analyse a patient VCF")
    if request.files:
        upload = request.files.get("file")
        if upload is None:
            raise APIError("Upload the VCF in the 'file' field")
        if request.form.get("test_data") not in ("1", "true", "on"):
            raise APIError("This prototype accepts test data only (synthetic mock VCFs). Please confirm the file "
                           "contains no real patient data.")
        raw = upload.read(current_app.config["MAX_VCF_BYTES"] + 1)
        variants, info = read_upload(raw, upload.filename)
        info["filename"] = upload.filename
        cancer_type, compare = request.form.get("cancer_type"), request.form.get("compare") in ("1", "true", "on")
        medications = parse_medications(request.form.get("medications"))
        clinical = parse_clinical(request.form)
    else:
        body = request.get_json(silent=True) or {}
        if not body.get("example"):
            raise APIError("Upload a VCF, or choose one of the synthetic examples")
        require_admin_for_examples()
        variants, info, example = load_example(body["example"])
        cancer_type = body.get("cancer_type") or example["cancer_type"]
        compare = bool(body.get("compare"))
        medications = parse_medications(body.get("medications"))
        clinical = parse_clinical(body.get("clinical"))
    return _query_response(run_vcf_exploration(variants, info, cancer_type, compare, medications, clinical), 201)


@bp.post("/ism")
def ism():
    """AlphaGenome in-silico mutagenesis heatmap around one QC-passed variant.
    JSON {chrom, pos, ref?, alt?, gene?, assembly?}; slow (tens of seconds), so the page asks for it on demand."""
    require_login("Log in to compute the heatmap")
    body = request.get_json(silent=True) or {}
    if body.get("chrom") is None or body.get("pos") is None:
        raise APIError("chrom and pos are required")
    return jsonify(alphagenome.ism_heatmap(body["chrom"], body["pos"], gene=body.get("gene"), ref=body.get("ref"),
                                           alt=body.get("alt"), assembly=body.get("assembly") or "GRCh38"))
