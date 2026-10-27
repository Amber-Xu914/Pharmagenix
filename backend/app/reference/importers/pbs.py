"""Australian PBS Schedule items for the current month, with their restrictions (indications and criteria).

The public API key is shared by all unregistered users and limited to one request per 20 seconds,
so the importer makes as few requests as possible (the whole schedule fits in one page).
"""

import time
from datetime import date

from flask import current_app

from ..models import PbsItem, PbsItemRestriction, PbsRestriction
from ..text import norm, strip_salt
from . import drug_synonym_index, http_session, lookup_drug, replace_tables

ITEM_FIELDS = ("pbs_code,drug_name,li_drug_name,brand_name,li_form,schedule_form,program_code,"
               "benefit_type_code,manner_of_administration,schedule_code")
RESTRICTION_FIELDS = "res_code,treatment_phase,authority_method,li_html_text,schedule_code"
LINK_FIELDS = "pbs_code,res_code,benefit_type_code"
PAGE_SIZE = 100000


def run(echo):
    cfg = current_app.config
    base = cfg["PBS_API_URL"].rstrip("/")
    session = http_session({"Subscription-Key": cfg["PBS_SUBSCRIPTION_KEY"]})
    interval = cfg["PBS_MIN_INTERVAL"]

    schedule = _get(session, f"{base}/schedules",
                    {"limit": 1, "sort": "desc", "sort_fields": "effective_date"}, interval, echo)["data"][0]
    code, effective = schedule["schedule_code"], schedule["effective_date"]
    echo(f"[pbs] current schedule {code} (effective {effective}); waiting {interval}s for the rate limit")

    items = _get_all(session, f"{base}/items", code, ITEM_FIELDS, interval, echo)
    restrictions = _get_all(session, f"{base}/restrictions", code, RESTRICTION_FIELDS, interval, echo)
    links = _get_all(session, f"{base}/item-restriction-relationships", code, LINK_FIELDS, interval, echo)

    rows = build_items(items, date.fromisoformat(effective), drug_synonym_index())
    restriction_rows, link_rows = build_restrictions(restrictions, links)
    replace_tables([(PbsItem, rows), (PbsRestriction, restriction_rows), (PbsItemRestriction, link_rows)])
    linked = sum(1 for r in rows if r["chembl_id"])
    return {"version": f"PBS Schedule {code}, effective {effective}", "url": base,
            "counts": {"pbs_items": len(rows), "linked_to_chembl": linked,
                       "pbs_restrictions": len(restriction_rows), "pbs_item_restrictions": len(link_rows)}}


def _get_all(session, url, schedule_code, fields, interval, echo):
    """Every record of one endpoint for the schedule (normally a single page), waiting out the rate limit first."""
    records, page = [], 1
    while True:
        time.sleep(interval)
        data = _get(session, url, {"schedule_code": schedule_code, "limit": PAGE_SIZE, "page": page,
                                   "fields": fields}, interval, echo)
        records += data["data"]
        if len(records) >= data["_meta"]["total_records"] or not data["data"]:
            return records
        page += 1


def _get(session, url, params, interval, echo, attempts=4):
    for attempt in range(attempts):
        resp = session.get(url, params=params, timeout=180)
        if resp.status_code != 429:
            resp.raise_for_status()
            return resp.json()
        echo(f"[pbs] rate limited; retrying in {interval}s")
        time.sleep(interval)
    resp.raise_for_status()


def build_items(items, effective_date, drug_index):
    rows = []
    for item in items:
        drug_name = item.get("drug_name") or item.get("li_drug_name")
        if not drug_name or not item.get("pbs_code"):
            continue
        rows.append({
            "pbs_code": item["pbs_code"],
            "drug_name": drug_name[:255],
            "drug_name_norm": strip_salt(drug_name)[:255],
            "chembl_id": lookup_drug(drug_index, drug_name),
            "brand_name": (item.get("brand_name") or "")[:255] or None,
            "brand_name_norm": norm(item.get("brand_name"))[:255] or None,
            "form": (item.get("li_form") or "")[:255],
            "schedule_form": (item.get("schedule_form") or "")[:500],
            "program_code": item.get("program_code"),
            "benefit_type_code": item.get("benefit_type_code"),
            "route": item.get("manner_of_administration"),
            "schedule_code": item.get("schedule_code"),
            "effective_date": effective_date,
        })
    return rows


def build_restrictions(restrictions, links):
    """Restriction rows (text unchanged: the PBS licence does not permit modification) and item links."""
    rows = {}
    for r in restrictions:
        if r.get("res_code") and r["res_code"] not in rows:
            rows[r["res_code"]] = {
                "res_code": r["res_code"],
                "treatment_phase": (r.get("treatment_phase") or "")[:255] or None,
                "authority_method": r.get("authority_method"),
                "html_text": r.get("li_html_text") or "",
                "schedule_code": r.get("schedule_code"),
            }
    seen, link_rows = set(), []
    for link in links:
        key = (link.get("pbs_code"), link.get("res_code"))
        if key[0] and key[1] in rows and key not in seen:
            seen.add(key)
            link_rows.append({"pbs_code": key[0], "res_code": key[1], "benefit_type_code": link.get("benefit_type_code")})
    return list(rows.values()), link_rows
