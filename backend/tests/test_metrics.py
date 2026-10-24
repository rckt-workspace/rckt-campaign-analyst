from app.domain.metrics import analyze_metrics, build_deterministic_block
from app.domain.schemas import CampaignInput


def campaign(**overrides):
    data = {"rawInput": "Ejemplo de prueba", "objective": "Ventas"}
    data.update(overrides)
    return CampaignInput(**data)


def test_calculated_metrics_and_comparisons():
    data = campaign(
        investment=4850000, impressions=410000, reach=245000,
        clicks=8610, leads=522, conversions=94, revenue=11960000,
        previous={"ctr": 1.65}, ctr=2.10,
    )
    report = analyze_metrics(data)
    assert report.calculated["ctr"] == 2.1
    assert report.calculated["roas"] == 2.466
    assert report.calculated["cpa"] == 51595.74
    assert report.calculated["frequency"] == 1.6735
    assert any(x["metric"] == "CTR" and x["changePercent"] == 27.27 for x in report.comparisons)
    assert "MÉTRICAS CALCULADAS POR LA APLICACIÓN" in build_deterministic_block(report)


def test_roas_mismatch_is_flagged():
    report = analyze_metrics(campaign(investment=100, revenue=200, roas=5))
    warnings = [w for w in report.warnings if w["type"] == "metric_mismatch"]
    assert len(warnings) == 1
    assert warnings[0]["metric"] == "ROAS"
    assert warnings[0]["calculated"] == 2


def test_missing_sales_and_impossible_values():
    report = analyze_metrics(campaign(impressions=10, clicks=15, investment=-1))
    assert any(w["type"] == "impossible_relationship" for w in report.warnings)
    assert any(w["type"] == "invalid_value" for w in report.warnings)
    assert any(w["type"] == "missing_critical_metric" for w in report.warnings)


def test_zero_denominators_not_infinite():
    report = analyze_metrics(campaign(impressions=0, clicks=0, investment=50))
    assert report.calculated["ctr"] is None
    assert report.calculated["cpc"] is None
    assert report.calculated["roas"] is None
