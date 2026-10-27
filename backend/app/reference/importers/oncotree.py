"""OncoTree cancer type hierarchy (latest stable release), the standard terms for cancer type / subtype."""

from flask import current_app

from ..models import OncotreeType
from . import http_session, replace_tables


def run(echo):
    base = current_app.config["ONCOTREE_API_URL"].rstrip("/")
    session = http_session()
    echo(f"[oncotree] downloading {base}/tumorTypes")
    resp = session.get(f"{base}/tumorTypes", params={"version": "oncotree_latest_stable"}, timeout=120)
    resp.raise_for_status()
    rows = build_types(resp.json())
    versions = session.get(f"{base}/versions", timeout=60).json()
    stable = next((v for v in versions if v.get("api_identifier") == "oncotree_latest_stable"), {})
    replace_tables([(OncotreeType, rows)])
    return {"version": f"OncoTree {stable.get('release_date', 'latest stable')}", "url": base,
            "counts": {"oncotree_types": len(rows)}}


def build_types(types):
    return [{
        "code": t["code"][:20],
        "name": t["name"][:255],
        "main_type": (t.get("mainType") or "")[:255] or None,
        "tissue": (t.get("tissue") or "")[:100] or None,
        "parent": t.get("parent") or None,
        "level": t.get("level"),
    } for t in types if t.get("code")]
