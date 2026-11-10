"""Offline results tests: fixtures are explicitly synthetic, never served as live data."""
import copy
import json

import httpx
import pytest

from act import api
from act.persistence import SupabasePersistence
from act.results import capture_results, decision_panel, equity_chart, load_reports, trend_rows


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Network forbidden in results tests")
    monkeypatch.setattr(httpx.Client, "send", forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden)


def sample(equity=10000):
    return {"finished_at": "2026-11-10T16:00:00+00:00", "paper_positions": [],
            "paper_account": {"equity": equity, "cash": equity},
            "valuation_status": "fresh_cycle_quotes"}


def test_snapshot_copies_account_without_mutating_ledger_values():
    report = sample()
    before = copy.deepcopy(report)
    capture_results(report)
    assert report["equity_snapshot"] == {
        "timestamp": before["finished_at"], "equity": 10000, "cash": 10000,
        "open_positions_count": 0, "valuation_status": "fresh_cycle_quotes"}
    for key, value in before.items():
        assert report[key] == value
    assert report["results_config"]["STRATEGY_MAX_OPEN_POSITIONS"] == 3
    assert report["results_config"]["MAX_RISK_PER_TRADE_PCT"] == 1
    assert not any("KEY" in key or "URL" in key for key in report["results_config"])


def test_snapshot_missing_equity_is_gap_not_zero():
    report = {"finished_at": "2026-11-10T16:00:00+00:00", "paper_account": {"error": "Missing marks"}}
    capture_results(report)
    assert report["equity_snapshot"]["equity"] is None
    assert report["equity_snapshot"]["cash"] is None
    assert report["equity_snapshot"]["open_positions_count"] is None


def test_chart_no_backfill_single_point_flat_and_gap():
    assert "No equity snapshots" in equity_chart([sample()])
    first = sample()
    capture_results(first)
    chart = equity_chart([first])
    assert chart.count("<circle ") == 1
    assert "no synthetic backfill" in chart
    second = copy.deepcopy(first)
    second["equity_snapshot"]["timestamp"] = "2026-11-10T16:20:00+00:00"
    chart = equity_chart([second, first])
    assert "10000 – 10000" in chart
    assert "nan" not in chart.lower()
    gap = copy.deepcopy(second)
    gap["equity_snapshot"].update(timestamp="2026-11-10T16:10:00+00:00", equity=None)
    chart = equity_chart([second, gap, first])
    assert "1 unavailable (gaps)" in chart
    assert chart.count("<polyline ") == 2


def test_decision_cap_escape_and_raw_provenance():
    decision = {"mint_address": "<unsafe>", "decision": "REJECT", "reasoning": "all reasons",
                "failure_reasons": ["missing_security_data", "failed_security_check"],
                "proposal": {"security_evidence": {"source": "missing", "raw_authority": None}}}
    page = decision_panel([{"decisions": [decision] * 205}])
    assert page.count("<details>") == 200
    assert "&lt;unsafe&gt;" in page and "<unsafe>" not in page
    assert "missing_security_data" in page and "failed_security_check" in page
    assert "raw_authority" in page and "null" in page


def test_trends_preserve_stored_multi_label_counts():
    reports = [{"finished_at": "new", "rejection_breakdown": {"missing_security_data": 3,
                "failed_security_check": 2}}, {"finished_at": "old"}]
    before = copy.deepcopy(reports)
    rows = trend_rows(reports)
    assert rows[0]["timestamp"] == "old"
    assert rows[0]["rejections"] is None
    assert rows[1]["rejections"]["missing_security_data"] == 3
    assert rows[1]["rejections"]["failed_security_check"] == 2
    assert reports == before


def test_durable_read_is_bounded_and_ordered():
    class Fake:
        def _request(self, method, path, **kwargs):
            assert method == "GET" and path == "cycles"
            assert kwargs["params"] == {"select": "report", "order": "completed_at.desc", "limit": "30"}
            return httpx.Response(200, json=[{"report": {"cycle_id": "real"}}])
    assert load_reports(Fake()) == [{"cycle_id": "real"}]


def test_malformed_history_fails_loudly():
    class Fake:
        def _request(self, *args, **kwargs):
            return httpx.Response(200, json=[{"report": None}])
    with pytest.raises(ValueError, match="Invalid persisted"):
        load_reports(Fake())


def test_local_history(tmp_path):
    class Local:
        directory = tmp_path
    (tmp_path / "cycle.json").write_text(json.dumps({"status": "success"}))
    assert load_reports(Local()) == [{"status": "success"}]


def test_routes_six_panels_no_mutations_and_no_network(monkeypatch):
    report = sample()
    capture_results(report)
    monkeypatch.setattr(api, "LATEST", report)
    monkeypatch.setattr(api, "RECENT", [report])
    monkeypatch.setattr(api, "HISTORY_ERROR", "<read failed>")
    page = api.dashboard()
    for section in ("equity", "positions", "trades", "decision-log", "risk", "cycle-trends"):
        assert f'id="{section}"' in page
    assert "No open paper positions." in page
    assert "No closed paper trades" in page
    assert "&lt;read failed&gt;" in page
    assert "https://" not in page
    assert api.status() is report
    assert api.health()["paper_only"] is True
    for route in api.app.routes:
        assert not (set(route.methods or []) & {"POST", "PUT", "PATCH", "DELETE"})


def test_loss_and_fees_not_recomputed():
    trade = {"opened_at": "2026-11-10T00:00:00+00:00", "closed_at": "2026-11-10T02:00:00+00:00",
             "realized_pnl": -31.25, "fees_paid": 2.75, "exit_reason": "stop_loss"}
    view = api.trade_views([trade])[0]
    assert view["holding_duration"] == "2:00:00"
    for key, value in trade.items():
        assert view[key] == value


def test_supabase_history_transport_is_not_used_by_render(monkeypatch):
    # Instantiating a store is safe; rendering never calls persistence.
    store = SupabasePersistence("https://example.supabase.co", "test-not-a-real-secret")
    assert store is not None
    monkeypatch.setattr(api, "LATEST", None)
    monkeypatch.setattr(api, "RECENT", [])
    assert "No equity snapshots" in api.dashboard()
