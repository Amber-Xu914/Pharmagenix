"""Australian PBS Schedule items for the current month.

The public API key is shared by all unregistered users and limited to one request per 20 seconds,
so the importer makes as few requests as possible (the whole schedule fits in one page).
"""

import time
from datetime import date

from flask import current_app

from ..models import PbsItem
from ..text import norm, strip_salt
from . import drug_synonym_index, http_session, lookup_drug, replace_tables

ITEM_FIELDS = ("pbs_code,drug_name,li_drug_name,brand_name,li_form,schedule_form,program_code,"
               "benefit_type_code,manner_of_administration,schedule_code")
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

    items, page = [], 1
    while True:
        time.sleep(interval)
        data = _get(session, f"{base}/items", {"schedule_code": code, "limit": PAGE_SIZE, "page": page,
                                               "fields": ITEM_FIELDS}, interval, echo)
        items += data["data"]
        if len(items) >= data["_meta"]["total_records"] or not data["data"]:
            break
        page += 1

    rows = build_items(items, date.fromisoformat(effective), drug_synonym_index())
    replace_tables([(PbsItem, rows)])
    linked = sum(1 for r in rows if r["chembl_id"])
    return {"version": f"PBS Schedule {code}, effective {effective}", "url": base,
            "counts": {"pbs_items": len(rows), "linked_to_chembl": linked}}


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
