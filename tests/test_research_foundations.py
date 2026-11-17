"""Offline research foundations; all price examples are synthetic fixtures, not market claims."""
from copy import deepcopy

import pytest

from act.research.registry import StrategyRegistry, baseline_registry
from act.research.evidence import metrics, news_context, promotion_gates, shadow_record
from act.research.replay import Bar, HoldoutBook, replay, walk_forward

AT = "2026-11-17T14:00:00+00:00"
LATER = "2026-11-17T15:00:00+00:00"


def version():
    r = StrategyRegistry()
    v = r.register("solana", {"threshold": [1]}, "fixture", AT)
    return r, v


def test_registry_copies_and_replays():
    r, v = version()
    r.baseline(v)
    snapshot = r.events
    snapshot[0]["version"]["parameters"]["threshold"].append(2)
    assert r.versions[v]["parameters"]["threshold"] == [1]
    assert StrategyRegistry(r.events).events == r.events
    with pytest.raises(ValueError):
        StrategyRegistry(snapshot)
    with pytest.raises(ValueError):
        r.baseline(v)
    challenger = r.register("solana", {"threshold": 2}, "challenger", LATER, v)
    assert challenger != v
    assert r.champions["solana"] == v


def test_registry_no_mutable_input_or_parent_forgery():
    r, v = version()
    p = {"x": []}
    c = r.register("solana", p, "fixture", LATER, v)
    p["x"].append(2)
    assert r.versions[c]["parameters"] == {"x": []}
    with pytest.raises(ValueError):
        r.register("spot", {}, "bad parent", AT, v)
    with pytest.raises(ValueError):
        r.register("solana", {}, "fixture", AT, "missing")
    with pytest.raises(ValueError):
        r.register("solana", {"nan": float("nan")}, "fixture", AT)


def test_all_markets_have_identity_only_champions():
    r = baseline_registry()
    assert set(r.champions) == {"solana", "spot", "futures"}
    assert len(r.versions) == 3
    assert all("not_captured" in v["parameters"]["configuration_status"] for v in r.versions.values())


def item(label="unverified", fetched=AT):
    return {"title": "Solana fixture headline", "verification": label,
            "sources": [{"fetched_at": fetched}], "url": "https://example.org/fixture"}


def test_news_cutoff_and_unverified_preserved():
    news = [item(), item("verified", LATER)]
    context = news_context(news, AT, ("solana",))
    assert context["n"] == 1
    assert context["items"][0]["verification"] == "unverified"
    context["items"][0]["title"] = "changed"
    assert news[0]["title"] != "changed"
    assert news_context(news, AT, ("bitcoin",))["n"] == 0


def test_news_later_corroboration_cannot_leak():
    row = item("verified")
    row["sources"].append({"fetched_at": LATER})
    assert news_context([row], AT)["n"] == 0
    with pytest.raises(ValueError):
        news_context([item()], AT, since=LATER)


def test_shadow_has_no_ledger_path():
    record = shadow_record("v", "solana", AT, {"action": "BUY"}, news_context([], AT))
    assert record["record_type"] == "shadow"
    assert record["ledger_eligible"] is False
    with pytest.raises(ValueError):
        shadow_record("v", "solana", LATER, {}, news_context([], AT))


def trade(identity, pnl, opened=AT, closed=LATER, **values):
    return {"id": identity, "realized_pnl": pnl, "opened_at": opened, "closed_at": closed,
            "strategy_version_id": "v", "record_type": "forward_paper", **values}


def test_attribution_is_version_and_record_type_specific():
    rows = [trade("a", 20), trade("b", -10), trade("c", 999, strategy_version_id="other"),
            trade("d", 999, record_type="shadow")]
    m = metrics(rows, "v", 100)
    assert m["n"] == 2
    assert m["expectancy"] == 5
    assert m["profit_factor"] == 2
    assert m["net_pnl_after_costs"] == 10
    assert m["closed_equity_max_dd"] == pytest.approx(10 / 120)
    assert m["small_n"]
    with pytest.raises(ValueError):
        metrics([rows[0], rows[0]], "v", 100)


def passed():
    return {"strategy_version_id": "v", "n": 100, "weeks": 8,
            "expectancy": 1, "profit_factor": 1.31}


def gates(m=None, **kw):
    return promotion_gates(m or passed(), **{**dict(shadow_n=1, backtest_status="completed",
        walk_forward_status="passed", marked_max_dd=.099), **kw})


def test_eligible_is_never_automatic_promotion():
    result = gates()
    assert result["eligible"]
    assert not result["promoted"]


@pytest.mark.parametrize("field,value,gate", [
    ("n", 99, "trades_ge_100"), ("weeks", 7.99, "weeks_ge_8"),
    ("expectancy", 0, "positive_expectancy_after_costs"),
    ("expectancy", None, "positive_expectancy_after_costs"),
    ("profit_factor", 1.3, "profit_factor_gt_1_3"),
    ("profit_factor", None, "profit_factor_gt_1_3")])
def test_each_metric_gate(field, value, gate):
    m = passed()
    m[field] = value
    result = gates(m)
    assert not result["eligible"]
    assert gate in result["reasons"]


@pytest.mark.parametrize("kw,gate", [
    ({"marked_max_dd": .1}, "max_dd_lt_10_percent"),
    ({"marked_max_dd": None}, "max_dd_lt_10_percent"),
    ({"shadow_n": 0}, "forward_shadow_available"),
    ({"backtest_status": "insufficient_data"}, "backtest_available"),
    ({"walk_forward_status": "insufficient_data"}, "walk_forward_consistency")])
def test_each_external_gate(kw, gate):
    assert gate in gates(**kw)["reasons"]


def test_empty_metrics_never_pass():
    m = metrics([], "v", 100)
    assert m["n"] == 0 and m["expectancy"] is None
    assert not promotion_gates(m)["eligible"]


def test_replay_uses_close_not_open_timestamp_and_reports_gaps():
    r, v = version()
    bars = (Bar(0, 59999, 10), Bar(120000, 179999, 12))
    seen = []
    def decide(prefix):
        seen.append(prefix)
        return prefix[-1].close
    times = ["1970-01-01T00:00:59+00:00", "1970-01-01T00:01:00+00:00"]
    result = replay(r.versions[v], {"fixture": bars}, times, decide, 60000)
    assert len(seen) == 1 and seen[0] == (bars[0],)
    assert result["coverage"]["fixture"]["gaps"] == [[60000, 120000]]
    assert result["n_trades"] == 0 and result["status"] == "insufficient_data"
    assert not result["coverage"]["fixture"]["interpolated"]


def test_futures_skips_without_calling_strategy():
    r = baseline_registry()
    v = r.versions[r.champions["futures"]]
    def bad(_):
        raise AssertionError("must not evaluate futures")
    result = replay(v, {}, [AT], bad, 60000)
    assert result["status"] == "skipped" and "451" in result["reason"]


def test_splits_and_single_use_holdout():
    assert walk_forward(5, 4, 2) == []
    assert walk_forward(12, 4, 2) == [
        {"in_sample": [0, 4], "out_of_sample": [4, 6]},
        {"in_sample": [6, 10], "out_of_sample": [10, 12]}]
    book = HoldoutBook()
    assert book.evaluate_once("version:dataset:4:6", lambda: 7) == 7
    restored = HoldoutBook(book.used)
    with pytest.raises(ValueError):
        restored.evaluate_once("version:dataset:4:6", lambda: 8)
    with pytest.raises(ValueError):
        walk_forward(10, 0, 2)


def test_registry_rejects_promotion_events_even_after_eligibility():
    r, v = version()
    before = deepcopy(r.events)
    with pytest.raises(ValueError):
        StrategyRegistry(before + [{"kind": "promotion", "version_id": v}])
    assert r.events == before
  
