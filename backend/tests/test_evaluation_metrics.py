"""Classification metrics of Workflow 4 (evaluation.py): drug recommendation (binary) and variant tiering
(multi-class), computed from pipeline outputs against the expected answers; and the admin endpoint."""

import json

from app import evaluation as ev


def output(established=(), tiers=None, down=()):
    return {"established": [{"drug": d, "down_ranked": d in down} for d in list(established) + list(down)],
            "tiers": tiers or {}}


T790M = ev.CASES[0]  # EGFR L858R + T790M: osimertinib yes; gefitinib / erlotinib / afatinib (resistance) no


def test_binary_metrics_count_each_kind_of_error():
    rows = [{"expected": True, "predicted": True}, {"expected": True, "predicted": False},
            {"expected": False, "predicted": True}, {"expected": False, "predicted": False},
            {"expected": False, "predicted": False}]
    m = ev.binary_metrics(rows)
    assert m["confusion"] == {"tp": 1, "fn": 1, "fp": 1, "tn": 2}
    assert (m["precision"], m["recall"], m["f1"], m["specificity"], m["accuracy"]) == (0.5, 0.5, 0.5, 0.6667, 0.6)


def test_drug_rows_score_resistance_and_list_unlabelled_extras():
    # osimertinib missed, erlotinib wrongly recommended despite T790M, amivantamab an unlabelled extra
    rows, extras = ev.drug_rows(output(["Erlotinib", "Amivantamab"], down=["Gefitinib"]), T790M["expected"])
    got = {r["drug"]: (r["expected"], r["predicted"]) for r in rows}
    assert got["Osimertinib"] == (True, False) and got["Erlotinib"] == (False, True)
    assert got["Gefitinib"] == (False, False)  # down-ranked = not recommended
    assert extras == ["Amivantamab"]


def test_no_therapy_case_counts_every_recommendation_as_false_positive():
    lusc = next(c for c in ev.CASES if c["example"] == "mock_lusc_no_targeted")
    rows, extras = ev.drug_rows(output(["Sapanisertib"]), lusc["expected"])
    assert {"drug": "Sapanisertib", "expected": False, "predicted": True} in rows and extras == []


def test_tier_confusion_matrix_and_accepted_alternatives():
    expected = {"tiers": {"A": 1, "B": [1, 2], "C": 3, "D": 2}}
    rows = ev.tier_rows({"tiers": {"A": 1, "B": 2, "C": 2}}, expected)  # B: accepted; C: wrong; D: missed
    m = ev.multiclass_metrics(rows)
    assert m["matrix"] == [[1, 0, 0, 0], [0, 1, 0, 1], [0, 1, 0, 0], [0, 0, 0, 0]]
    assert m["accuracy"] == 0.5 and m["per_class"]["Tier 3"]["recall"] == 0.0
    assert "Not tiered" in m["per_class"] and m["per_class"]["Not tiered"]["support"] == 0  # predicted, never expected


def test_score_outputs_perfect_case():
    exp = T790M["expected"]
    result = ev.score_outputs({T790M["example"]: output(["Osimertinib"], {v: (t[0] if isinstance(t, list) else t)
                                                                           for v, t in exp["tiers"].items()})})
    assert result["drug_recommendation"]["f1"] == 1.0 and result["variant_tiering"]["accuracy"] == 1.0
    assert result["cases"][0]["tiers_correct"] == len(exp["tiers"])


def test_admin_evaluation_endpoint(client, admin_headers, tmp_path, monkeypatch):
    path = tmp_path / "pipeline_metrics.json"
    monkeypatch.setattr(ev, "METRICS_PATH", str(path))
    resp = client.get("/api/admin/evaluation", headers=admin_headers)
    assert resp.status_code == 404 and "eval-metrics" in resp.get_json()["error"]
    path.write_text(json.dumps({"drug_recommendation": {"f1": 1.0}}))
    assert client.get("/api/admin/evaluation", headers=admin_headers).get_json()["drug_recommendation"]["f1"] == 1.0
    assert client.get("/api/admin/evaluation").status_code == 401  # admins only
