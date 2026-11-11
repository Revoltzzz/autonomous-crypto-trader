"""Offline learning evidence tests: all HTTP is MockTransport, never live data."""
import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace

import httpx
import pytest

from act import learning as l

AT = "2026-01-01T00:00:00+00:00"
DUE = "2026-01-01T01:00:00+00:00"
CFG = {"budget": 2, "backlog": 200, "grace_seconds": 3600}


def report(verdict="REJECT", mint="mint", sid="decision"):
    return {"cycle_id": "cycle", "decisions": [{"id": sid, "created_at": AT,
        "mint_address": mint, "pool_address": "pool", "symbol": "<TEST>",
        "decision": verdict, "failure_reasons": ["volume"], "proposal": {}}],
        "discoveries": [{"mint_address": mint, "price_usd": 2, "liquidity_usd": 100,
                         "volume_24h_usd": 50, "source_url": "offline", "discovered_at": AT}]}


def state():
    s = l.initial(AT)
    l.register(s, report(), AT)
    return s


def gt(price=3, liquidity=120, volume=40, mint="mint"):
    return {"data": {"attributes": {"address": "pool", "base_token_price_usd": price,
        "reserve_in_usd": liquidity, "volume_usd": {"h24": volume}},
        "relationships": {"base_token": {"data": {"id": "solana_" + mint}}}}}


def execute(tmp_path, handler, *, old=None, at=DUE, cfg=None, payload=None):
    if old is not None:
        (tmp_path / "checkpoint.json").write_text(json.dumps({"learning": {"state": old}}))
    result = payload or {"cycle_id": "later", "decisions": []}

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await l.track(result, SimpleNamespace(directory=tmp_path), client=client,
                          at=at, cfg=cfg or CFG)
    asyncio.run(run())
    return result["learning"]


@pytest.mark.parametrize("verdict", ["BUY", "REJECT", "WATCH"])
def test_every_verdict_and_repeat_reference(verdict):
    s = l.initial(AT)
    first = report(verdict)
    l.register(s, first, AT)
    repeat = report("WATCH", sid="later")
    l.register(s, repeat, DUE)
    assert len(s["active"]) == 1
    assert repeat["decisions"][0]["learning_session_id"] == "decision"
    assert s["active"][0]["verdict"] == verdict
    assert s["active"][0]["price_usd"] == 2


def test_horizons_due_boundary_and_oldest_first():
    s = state()
    assert not l.prepare(s, AT, 200, 3600)[0]
    pending, events = l.prepare(s, DUE, 200, 3600)
    assert len(pending) == 1 and pending[0][2] == "1h" and events == []
    later = report(mint="new", sid="new")
    later["decisions"][0]["created_at"] = "2026-01-01T00:10:00+00:00"
    l.register(s, later, DUE)
    pending, _ = l.prepare(s, "2026-01-01T01:20:00+00:00", 200, 3600)
    assert [p[1] for p in pending] == ["decision", "new"]


def test_sleep_gap_no_interpolation():
    s = state()
    pending, events = l.prepare(s, "2026-01-02T12:00:00+00:00", 200, 3600)
    assert not pending
    assert {e["horizon"] for e in events} == {"1h", "6h", "24h"}
    assert all(e["status"] == "missed_service_gap_or_budget" and e["return_pct"] is None
               for e in events)
    assert len(s["active"][0]["observations"]) == 3


def test_backlog_evicts_oldest_with_explicit_terminal_events():
    s = state()
    l.register(s, report(mint="new", sid="new"), AT)
    _, events = l.prepare(s, AT, 1, 3600)
    assert len(s["active"]) == 1 and s["active"][0]["decision_id"] == "new"
    assert len(events) == 5
    assert all(e["status"] == "missed_backlog_cap" for e in events)
    assert s["seen"]["mint"] == "decision"


def test_no_backfill_no_calls_before_due(tmp_path):
    calls = []
    result = execute(tmp_path, lambda r: calls.append(r), at=AT, payload=report())
    assert not calls and result["stats"]["pending"] == 5
    assert result["stats"]["budget_used"] == 0
    assert result["events"] == []


def test_observed_return_is_derived_from_immutable_baseline(tmp_path):
    old = state()
    original = deepcopy(old)
    result = execute(tmp_path, lambda r: httpx.Response(200, json=gt()), old=old)
    e = result["events"][0]
    assert e["return_pct"] == 50 and e["liquidity_change_pct"] == pytest.approx(20)
    assert e["due_at"] == DUE and e["observed_at"] == DUE
    assert e["attempts"][0]["source"] == "geckoterminal"
    assert result["stats"]["budget_used"] == 1
    assert old == original
    assert result["distributions"]["outcomes"]["1h"]["median"] == 50
    assert result["calibration"][0]["n"] == 1


@pytest.mark.parametrize("budget", [0, 1, 2])
def test_budget_counts_each_source_request(tmp_path, budget):
    calls = []
    def handler(r):
        calls.append(str(r.url))
        return httpx.Response(503)
    result = execute(tmp_path, handler, old=state(), cfg={**CFG, "budget": budget})
    assert len(calls) == budget == result["stats"]["budget_used"]
    if budget:
        assert result["events"][0]["status"] == "data_missing"


def test_429_isolates_source_for_rest_of_cycle(tmp_path):
    s = state()
    l.register(s, report(mint="second", sid="second"), AT)
    calls = []
    def handler(r):
        calls.append(r.url.host)
        return httpx.Response(429) if "gecko" in r.url.host else httpx.Response(200, json=[])
    result = execute(tmp_path, handler, old=s, cfg={**CFG, "budget": 6})
    assert calls == ["api.geckoterminal.com", "api.dexscreener.com", "api.dexscreener.com"]
    assert result["stats"]["budget_used"] == 3
    assert len(result["events"]) == 2
    assert result["events"][0]["attempts"][0]["status"] == "rate_limited"


@pytest.mark.parametrize("code,status", [(404, "pool_not_found"), (503, "data_missing")])
def test_pool_absence_not_missing_or_fabricated_collapse(tmp_path, code, status):
    result = execute(tmp_path, lambda r: httpx.Response(code), old=state())
    e = result["events"][0]
    assert e["status"] == status and e["return_pct"] is None
    assert e["pool_exists"] is (False if code == 404 else None)
    if code == 404:
        assert "not proof" in e["availability_scope"]


def test_zero_liquidity_and_partial_data_not_interpolated(tmp_path):
    result = execute(tmp_path, lambda r: httpx.Response(200, json=gt(None, 0, None)), old=state())
    e = result["events"][0]
    assert e["status"] == "partial" and e["liquidity_drained"] is True
    assert e["return_pct"] is None and e["liquidity_change_pct"] == -100


def test_wrong_base_identity_is_not_a_price(tmp_path):
    def handler(r):
        return httpx.Response(200, json=gt(mint="wrong")) if "gecko" in r.url.host else httpx.Response(503)
    result = execute(tmp_path, handler, old=state())
    assert result["events"][0]["return_pct"] is None
    assert "identity mismatch" in result["events"][0]["attempts"][0]["error"]


def test_checkpoint_skips_newer_cycle_without_learning(tmp_path):
    (tmp_path / "old.json").write_text(json.dumps({"learning": {"state": state()}}))
    (tmp_path / "new.json").write_text(json.dumps({"learning_error": "offline failure"}))
    assert l.load_checkpoint(SimpleNamespace(directory=tmp_path))["state"]["seen"] == {"mint": "decision"}


def test_remote_checkpoint_query_and_invalid_response():
    class Remote:
        def _request(self, method, path, params):
            assert method == "GET" and path == "cycles"
            assert params["report->learning"] == "not.is.null"
            return httpx.Response(200, json=[{"learning": {"state": state()}}])
    assert l.load_checkpoint(Remote())["state"]["version"] == 1


def test_distributions_missingness_n_and_security_source():
    r = report()
    r["decisions"][0]["proposal"] = {"intelligence": {"ta": {"indicators": {
        "rsi14": {"value": 60, "available": True}}}}, "security_evidence": {"fields": {
        "mint_authority_revoked": {"effective_value": True, "source": "onchain"}}}}
    result = l.distributions(r, [])
    assert result["ta"]["rsi14"] == {"n": 1, "missing": 0, "min": 60, "median": 60, "max": 60}
    assert result["ta"]["ema9"]["n"] == 0 and result["ta"]["ema9"]["missing"] == 1
    assert result["security_values_by_source"]["mint_authority_revoked:onchain"]["values"] == {"true": 1}
    assert result["missing_fields"]["security.freeze_authority_revoked"]["n_missing"] == 1
    assert result["market"]["price_usd"]["median"] == 2
    assert result["rejection_reasons"]["volume"]["n"] == 1


def test_learning_dashboard_empty_escape_and_stored_aggregates(tmp_path):
    result = execute(tmp_path, lambda r: pytest.fail("No HTTP before due"), at=AT, payload=report())
    latest = {"learning": result}
    html = l.learning_panel(latest, [latest])
    assert 'id="learning"' in html and "SMALL-N WARNING" in html
    assert "First horizons pending" in html and "&lt;TEST&gt;" in html
    assert "No backfill" in html and "n returns" in html
    assert "Outcome tracking not recorded" in l.learning_panel({}, [])


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, True, None, "bad"])
def test_invalid_quote_values_missing(value):
    assert l.number(value) is None


def test_invalid_config_loud(monkeypatch):
    monkeypatch.setenv("LEARNING_FOLLOWUP_BUDGET", "21")
    with pytest.raises(ValueError, match="collection bounds"):
        l.config()
