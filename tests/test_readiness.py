"""Offline synthetic fixtures only; never appended to production records."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

from act.readiness import (compute, capture_readiness, date, health_flags,
                           load_cycles, project, readiness_panel)

AT = "2026-11-12T12:00:00+00:00"
START = "2026-09-17T12:00:00+00:00"


def cycle(cid="old", equity=100, at=START):
    return {"cycle_id": cid, "started_at": at, "finished_at": at, "status": "success",
            "persistence": {"status": "persisted"}, "collectors": {},
            "equity_snapshot": {"timestamp": at, "equity": equity}}


def report(pnls=(), equity=100):
    return dict(cycle("current", equity, AT), paper_account={"equity": equity},
        paper_positions=[], paper_trades=[{
            "id": str(i), "opened_at": START, "closed_at": AT,
            "realized_pnl": pnl, "fees_paid": 2, "entry_price": 10, "exit_price": 11,
            "exit_reason": ["stop_loss", "take_profit", "max_age"][i % 3]}
            for i, pnl in enumerate(pnls)])


def result(pnls=(), cycles=None, equity=100):
    return compute(report(pnls, equity), [cycle()] if cycles is None else cycles, AT)


def test_hand_computed_costs_expectancy_pf_winrate():
    m = result([10, -5, 15])["metrics"]
    assert m["net_expectancy_after_fees"]["value"] == pytest.approx(20 / 3)
    assert m["profit_factor"]["value"] == 5
    assert m["win_rate_pct"]["value"] == pytest.approx(200 / 3)
    assert m["exit_reasons"]["value"] == {"stop_loss": 1, "take_profit": 1, "max_age": 1}
    assert m["duration_hours"]["value"]["mean"] == 56 * 24
    assert m["net_expectancy_after_fees"]["n"] == 3


@pytest.mark.parametrize("pnls", [[], [1], [1, 2], [0, 0]])
def test_no_losses_is_undefined_never_infinity(pnls):
    r = result(pnls)
    assert r["metrics"]["profit_factor"]["value"] is None
    assert r["gates"]["profit_factor"]["status"] == "insufficient_data"
    assert r["metrics"]["profit_factor"]["n"] == len(pnls)
    json.dumps(r, allow_nan=False)


def test_empty_ledger_not_zero_filled_performance():
    r = result()
    assert r["metrics"]["trades_completed"]["value"] == 0
    assert r["gates"]["trades_completed"]["status"] == "fail"
    for key in ("net_expectancy_after_fees", "profit_factor", "duration_hours", "exit_reasons", "win_rate_pct"):
        assert r["metrics"][key]["value"] is None
    assert r["gates"]["weeks_elapsed"]["status"] == "insufficient_data"
    assert r["gates"]["out_of_sample"]["status"] == "insufficient_data"
    assert "awaiting data" in r["gates"]["out_of_sample"]["caveats"]


def test_single_trade_descriptive_value_but_insufficient_gate():
    r = result([-3])
    assert r["metrics"]["net_expectancy_after_fees"]["value"] == -3
    assert r["metrics"]["profit_factor"]["value"] == 0
    assert r["gates"]["net_expectancy_after_fees"]["status"] == "insufficient_data"


@pytest.mark.parametrize("pnls,status", [([1, -1], "fail"), ([2, -1], "pass"), ([-2, -1], "fail")])
def test_expectancy_strict_positive(pnls, status):
    assert result(pnls)["gates"]["net_expectancy_after_fees"]["status"] == status


@pytest.mark.parametrize("pnls,status", [([13, -10], "fail"), ([13.1, -10], "pass")])
def test_pf_strict_boundary(pnls, status):
    assert result(pnls)["gates"]["profit_factor"]["status"] == status


@pytest.mark.parametrize("count,status", [(99, "fail"), (100, "pass")])
def test_trade_count_boundary(count, status):
    assert result([1] * count)["gates"]["trades_completed"]["status"] == status


def test_both_week_baselines_and_sleep_not_uptime():
    r = result([1, -1])
    assert r["metrics"]["weeks_since_first_persisted_cycle"]["value"] == 8
    assert r["metrics"]["weeks_since_first_trade"]["value"] == 8
    assert r["gates"]["weeks_elapsed"]["status"] == "pass"
    current = report([1])
    current["paper_trades"][0]["opened_at"] = AT
    assert compute(current, [cycle()], AT)["gates"]["weeks_elapsed"]["status"] == "fail"
    assert result([1], cycles=[])["gates"]["weeks_elapsed"]["status"] == "insufficient_data"


@pytest.mark.parametrize("equities,expected", [([100, 120, 90, 110], 25), ([100, 100], 0), ([100, 90], 10)])
def test_drawdown_peak_to_trough(equities, expected):
    base = datetime(2026, 11, 10, tzinfo=timezone.utc)
    cycles = [cycle(str(i), e, (base + timedelta(hours=i)).isoformat()) for i, e in enumerate(equities)]
    current = report(equity=equities[-1])
    r = compute(current, cycles, AT)
    assert r["metrics"]["max_drawdown_pct"]["value"] == pytest.approx(expected)
    assert r["metrics"]["max_drawdown_pct"]["n"] == len(equities) + 1
    assert r["gates"]["max_drawdown_pct"]["status"] == ("pass" if expected < 10 else "fail")


def test_dd_partial_windows_and_one_point():
    r = result(cycles=[cycle(equity=None)], equity=100)
    assert r["gates"]["max_drawdown_pct"]["status"] == "insufficient_data"
    assert r["metrics"]["max_drawdown_pct"]["value"] is None
    r = result(cycles=[cycle(equity=120), cycle("gap", None, "2026-11-11T00:00:00+00:00")], equity=90)
    assert r["metrics"]["max_drawdown_pct"]["value"] == 25
    assert "1 missing/invalid" in r["metrics"]["max_drawdown_pct"]["caveats"]
    assert "not intracycle" in r["metrics"]["max_drawdown_pct"]["caveats"]


@pytest.mark.parametrize("field", ["realized_pnl", "fees_paid", "entry_price", "exit_price"])
def test_missing_recorded_cost_or_pnl(field):
    current = report([1, -1])
    current["paper_trades"][0].pop(field)
    assert compute(current, [cycle()], AT)["gates"]["net_expectancy_after_fees"]["status"] == "insufficient_data"


def test_missing_ledger_is_unknown_not_no_trades():
    r = compute(cycle("current", 100, AT), [cycle()], AT)
    assert r["metrics"]["trades_completed"]["value"] is None
    assert r["open_context"]["n"] is None


def test_health_fields_precisely_and_no_unrecorded_certification():
    c = cycle()
    c.update(status="failed", strategy_error="x", learning_error="x", error={"message": "x"},
             collectors={"CoinGecko": {"status": "degraded"}},
             persistence={"status": "failed"}, position_management={"stale_tokens": ["x"]})
    flags = health_flags(c)
    assert len(flags) == 7
    assert "collectors.CoinGecko.status/error" in flags
    r = result(cycles=[c])
    assert r["gates"]["critical_issues"]["status"] == "fail"
    assert "No resolution registry" in r["gates"]["critical_issues"]["caveats"]
    assert result()["gates"]["critical_issues"]["status"] == "pass"
    assert result(cycles=[])["gates"]["critical_issues"]["status"] == "insufficient_data"


def test_replay_provenance_no_mutation_and_post_commit_normalization():
    current, previous = report([5, -2]), [cycle()]
    before = deepcopy((current, previous))
    r = compute(current, previous, AT)
    assert (current, previous) == before
    current["persistence"] = {"status": "persisted"}
    assert compute(current, previous, AT) == r
    assert compute(current, [project(c) for c in previous], AT) == r
    assert r["provenance"]["closed_trade_ids"] == ["0", "1"]
    previous[0]["equity_snapshot"]["equity"] = 111
    assert compute(current, previous, AT)["provenance"]["cycles_sha256"] != r["provenance"]["cycles_sha256"]


def test_open_unrealized_does_not_enter_closed_metrics():
    current = report()
    current["paper_positions"] = [{"id": "open", "opened_at": START, "unrealized_pnl": 1000}]
    r = compute(current, [cycle()], AT)
    assert r["open_context"]["n"] == 1
    assert r["metrics"]["net_expectancy_after_fees"]["value"] is None
    assert r["metrics"]["weeks_since_first_trade"]["value"] == 8


def test_pagination_projects_stored_data_and_never_uses_thirty_cycle_limit():
    calls = []
    def request(method, path, params):
        calls.append(params)
        rows = [cycle(str(i)) for i in range(100)] if params["offset"] == "0" else []
        return SimpleNamespace(json=lambda: rows)
    rows = load_cycles(SimpleNamespace(_request=request), AT)
    assert len(rows) == 100
    assert len(calls) == 2
    assert calls[1]["offset"] == "100"
    assert "report->equity_snapshot" in calls[0]["select"]
    assert calls[0]["completed_at"] == "lt." + AT


def test_capture_failure_loud_but_never_changes_strategy_state(caplog):
    current = report([1, -1])
    before = deepcopy(current)
    capture_readiness(current, None)
    assert "READINESS_FAILED" in caplog.text
    assert current.pop("readiness_error")
    assert current == before


def test_capture_local_forward_only(tmp_path):
    old = tmp_path / "old"
    old.mkdir()
    path = old / "cycle.json"
    path.write_text(json.dumps(cycle()))
    before = path.read_text()
    new = tmp_path / "new"
    new.mkdir()
    current = report()
    capture_readiness(current, SimpleNamespace(directory=new))
    assert current["readiness"]["provenance"]["persisted_cycle_ids"] == ["old"]
    assert path.read_text() == before
    assert "readiness" not in json.loads(before)


def test_panel_escaped_and_empty_and_history():
    assert "Readiness unavailable" in readiness_panel({}, [])
    current = report([1, -1])
    current["readiness"] = compute(current, [cycle()], AT)
    current["readiness"]["health_flags"] = ["<script>alert(1)</script>"]
    html = readiness_panel(current, [current] * 40)
    assert "of 7 gates passing" in html
    assert "SMALL-N" in html
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "DISPLAY ONLY" in html
    assert html.count("<tr><td>" + AT + "</td><td>") == 30


def test_bad_dates_and_nonfinite_are_not_fabricated():
    assert date("2026-11-12") is None
    assert date(None) is None
    current = report([float("nan")])
    # Reject non-JSON raw provenance rather than persisting fabricated replacements.
    with pytest.raises(ValueError):
        compute(current, [cycle()], AT)
    with pytest.raises(ValueError):
        compute(report(), [], "2026-11-12")
