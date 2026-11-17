"""Synthetic offline fixtures; no claims about live returns or market coverage."""
import asyncio
from copy import deepcopy
import hashlib
import json

import httpx
import pytest

from act.agents import Context, freeze
from act.agents.market_reporting import mover_panel, scanner_panel
from act.agents.movers import LOSER_LABEL, configured_top_n, selection_plan
from act.agents.scanner import MarketScannerAgent, universe
from act.persistence import LocalPersistence
from test_binance_agents import fixture_response, info, tickers


def plan(rows, n=1, gap=None):
    return selection_plan(rows, {}, top_n=n, cycle_id="fixture-cycle", as_of="fixture-time",
                          provenance={"fetched_at": "fixture-time"}, gap=gap)


def fixture_rows(changes=(10, -20, 3)):
    source = tickers()
    for row, change in zip(source, changes):
        row["priceChangePercent"] = str(change)
    return universe(info("spot"), source, "spot")[1]


def test_order_priority_rotation_and_no_input_mutation():
    rows = fixture_rows()
    original = deepcopy(rows)
    batch, snapshot = plan(rows)
    assert [r["symbol"] for r in batch] == ["AAAUSDT", "BBBUSDT", "CCCUSDT"]
    assert [r["selection"]["basis"] for r in batch] == ["top_gainer", "top_loser", "rotation"]
    assert batch[0]["selection"]["priceChangePercent"] == 10
    assert batch[1]["selection"]["rank"] == 1
    assert batch[1]["research_label"] == LOSER_LABEL
    assert batch[2]["selection"]["rotation_index"] == 2
    assert rows == original
    payload = snapshot["payload_json"]
    assert hashlib.sha256(payload.encode()).hexdigest() == snapshot["sha256"]
    ranking = json.loads(payload)
    assert ranking["gainers"][0]["symbol"] == "AAAUSDT"
    assert ranking["losers"][0]["priceChangePercent"] == -20
    assert ranking["cycle_id"] == "fixture-cycle"


def test_ties_are_alphabetic_and_zero_is_not_a_mover():
    batch, snapshot = plan(fixture_rows((5, 5, 0)), n=2)
    ranking = json.loads(snapshot["payload_json"])
    assert [r["symbol"] for r in ranking["gainers"]] == ["AAAUSDT", "BBBUSDT"]
    assert ranking["losers"] == []
    assert batch[-1]["selection"]["basis"] == "rotation"
    _, snapshot = plan(fixture_rows((-5, -5, -1)), n=2)
    assert [r["symbol"] for r in json.loads(snapshot["payload_json"])["losers"]] == [
        "AAAUSDT", "BBBUSDT", "CCCUSDT"]


def test_full_plan_unique_and_negative_rotation_labeled():
    batch, _ = plan(fixture_rows((-1, -2, -3)), n=1)
    assert len({r["symbol"] for r in batch}) == 3
    assert all(r["research_label"] == LOSER_LABEL for r in batch)
    assert batch[0]["symbol"] == "CCCUSDT"
    batch, _ = plan(fixture_rows(), n=0)
    assert all(r["selection"]["basis"] == "rotation" for r in batch)


@pytest.mark.parametrize("value", ["-1", "11", "1.5", "", "nan", "infinity"])
def test_config_rejects_invalid(monkeypatch, value):
    monkeypatch.setenv("BINANCE_SPOT_MOVER_TOP_N", value)
    with pytest.raises(ValueError, match="0..10"):
        configured_top_n()


@pytest.mark.parametrize("value", [0, 1, 2, 10])
def test_config_bounds(monkeypatch, value):
    monkeypatch.setenv("BINANCE_SPOT_MOVER_TOP_N", str(value))
    assert configured_top_n() == value


def test_config_default(monkeypatch):
    monkeypatch.delenv("BINANCE_SPOT_MOVER_TOP_N", raising=False)
    assert configured_top_n() == 1


async def scan(handler, budget=6, old=None):
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        ctx = Context(freeze({"cycle_id": "fixture-cycle", "scanner_state": old or {}}),
                      client, 10, budget)
        result = await MarketScannerAgent("spot").run(ctx)
        return result, ctx.used


def mover_handler(request):
    if request.url.path.endswith("ticker/24hr"):
        source = tickers()
        for row, change in zip(source, (10, -20, 3)):
            row["priceChangePercent"] = str(change)
        return httpx.Response(200, json=source)
    return fixture_response(request)


@pytest.mark.parametrize("budget,expected", [(2, 0), (3, 0), (4, 1), (6, 2), (8, 3)])
def test_budget_and_completed_provenance(monkeypatch, budget, expected):
    monkeypatch.setenv("BINANCE_SPOT_MOVER_TOP_N", "1")
    result, used = asyncio.run(scan(mover_handler, budget))
    assert result["coverage"]["scanned_this_cycle"] == expected
    assert used <= budget
    assert result["coverage"]["weight_used"] <= 200
    selected = result["coverage"]["selections"]
    assert [s["basis"] for s in selected] == ["top_gainer", "top_loser", "rotation"][:expected]
    assert result["coverage"]["mover_priority_scanned"] == min(2, expected)
    assert result["coverage"]["rotation_scanned"] == max(0, expected - 2)
    for row in result["candidates"]:
        assert row["selection"]["snapshot_sha256"] == result["mover_snapshot"]["sha256"]
        if row["symbol"] == "BBBUSDT":
            assert row["research_label"] == LOSER_LABEL


def test_no_extra_requests_or_weight(monkeypatch):
    monkeypatch.setenv("BINANCE_SPOT_MOVER_TOP_N", "0")
    baseline, base_used = asyncio.run(scan(mover_handler))
    monkeypatch.setenv("BINANCE_SPOT_MOVER_TOP_N", "2")
    prioritized, used = asyncio.run(scan(mover_handler))
    assert used == base_used == 6
    assert prioritized["coverage"]["weight_used"] == baseline["coverage"]["weight_used"] == 114


@pytest.mark.parametrize("problem", ["network", "missing_change", "partial", "duplicate", "nan"])
def test_ticker_gap_loud_rotation_no_stale_ranking(problem, caplog):
    def handler(request):
        if not request.url.path.endswith("ticker/24hr"):
            return fixture_response(request)
        if problem == "network":
            raise httpx.ConnectError("offline fixture", request=request)
        source = tickers()
        if problem == "missing_change":
            del source[0]["priceChangePercent"]
        elif problem == "partial":
            source.pop()
        elif problem == "duplicate":
            source.append(deepcopy(source[0]))
        else:
            source[0]["priceChangePercent"] = "nan"
        return httpx.Response(200, json=source)
    result, used = asyncio.run(scan(handler, old={"spot": {"seen": {}, "mover_snapshot": "stale"}}))
    assert result["status"] == "degraded"
    assert result["coverage"]["scanned_this_cycle"] > 0
    assert used <= 6
    snapshot = json.loads(result["mover_snapshot"]["payload_json"])
    assert snapshot["status"] == "rotation_fallback"
    assert snapshot["data_gaps"] and not snapshot["gainers"] and not snapshot["losers"]
    assert snapshot["ticker_provenance"] is None
    assert all(r["selection"]["basis"] == "rotation" for r in result["candidates"])
    assert all(r["score"] is None and r["change_pct_24h"] is None for r in result["candidates"])
    assert any("SPOT_MOVER_ROTATION_FALLBACK" in r.message for r in caplog.records)


def test_snapshot_detached_immutable_and_append_per_cycle(tmp_path):
    rows = fixture_rows()
    batch, snapshot = plan(rows)
    payload = snapshot["payload_json"]
    batch[0]["change_pct_24h"] = 999
    rows.clear()
    assert snapshot["payload_json"] == payload
    frozen = freeze({"mover_snapshot": snapshot})
    with pytest.raises(TypeError):
        frozen["mover_snapshot"]["payload_json"] = "changed"
    store = LocalPersistence(tmp_path)
    store.save({"cycle_id": "first", "mover_snapshot": snapshot}, None)
    first = (tmp_path / "first.json").read_bytes()
    _, second = plan(fixture_rows((-2, 3, 7)))
    store.save({"cycle_id": "second", "mover_snapshot": second}, None)
    assert (tmp_path / "first.json").read_bytes() == first
    assert snapshot["sha256"] != second["sha256"]


def test_hud_stored_escape_checksum_and_wrap():
    rows = fixture_rows()
    rows[0]["symbol"] = "<script>" + "X" * 200
    _, snapshot = plan(rows)
    data = {"mover_snapshot": snapshot, "coverage": {"selections": [{"symbol": "<bad>"}],
            "mover_priority_scanned": 1, "rotation_scanned": 0}}
    html = scanner_panel({"agents": {"scanner_spot": {"data": data}}})
    assert "PAPER MODE" in html and "LAST STORED STATE" in html
    assert "&lt;script&gt;" in html and "<script>" not in html and "<bad>" not in html
    assert "overflow-wrap:anywhere" in mover_panel(data)
    assert "LONG-ONLY" in html and "priceChangePercent" in html
    bad = deepcopy(data)
    bad["mover_snapshot"]["payload_json"] += " "
    assert "INVALID" in mover_panel(bad)
