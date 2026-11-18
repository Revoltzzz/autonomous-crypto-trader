"""Offline checkpoint/holding fixtures; no external network or market claims."""
from copy import deepcopy
import json
from types import SimpleNamespace

import httpx
import pytest

from act.config.settings import Settings
from act.learning import config
from act.persistence import LocalPersistence, PersistenceError, SupabasePersistence
from act.research.checkpoint import capture_registry, load_registry, validate
from act.research.holding import holding_news

AT = "2026-11-18T00:00:00+00:00"
MID = "2026-11-18T01:00:00+00:00"
END = "2026-11-18T02:00:00+00:00"


def report():
    return {"cycle_id": "fixture", "paper_only": True, "started_at": AT}


def test_registry_local_restart_and_detached_pointers(tmp_path):
    store = LocalPersistence(tmp_path)
    r = report()
    state = capture_registry(r, store, Settings())
    r["research_registry"] = state
    store.save(r, None)
    restored = capture_registry({**r, "started_at": END}, store, Settings())
    assert restored["events"] == state["events"]
    assert restored["champions"] == state["champions"]
    assert set(restored["champions"]) == {"solana", "spot", "futures"}
    assert restored["activated_at"] == AT
    assert restored["observed_at"] == END
    restored["champions"].clear()
    assert len(load_registry(store)["champions"]) == 3
    assert len(state["events"]) == 9
    assert len(state["policy_observations"]) == 3
    assert "disabled" in state["attribution_status"]
    assert "disabled" in state["promotion_status"]


def test_registry_cost_drift_does_not_rewrite_identity(tmp_path):
    store = LocalPersistence(tmp_path)
    old = capture_registry(report(), store, Settings())
    store.save({**report(), "research_registry": old}, None)
    new = capture_registry(report(), store, Settings(fee_pct=.3))
    assert new["configuration_drift"]
    assert new["events"][:len(old["events"])] == old["events"]
    assert len(new["events"]) == len(old["events"]) + 3
    assert new["champions"] == old["champions"]
    assert old["registration_cost_settings"] == new["registration_cost_settings"]
    assert new["current_cost_settings"]["fee_pct"] == .3


@pytest.mark.parametrize("damage", ["schema", "champions", "events", "paper_only"])
def test_corrupt_checkpoint_never_resets(tmp_path, damage):
    store = LocalPersistence(tmp_path)
    old = capture_registry(report(), store, Settings())
    bad = deepcopy(old)
    bad[damage] = {"schema": 9, "champions": {}, "events": [], "paper_only": False}[damage]
    store.save({**report(), "research_registry": bad}, None)
    with pytest.raises((ValueError, KeyError, TypeError)):
        capture_registry(report(), store, Settings())


def test_supabase_round_trip_uses_existing_transaction(tmp_path):
    checkpoint = capture_registry(report(), LocalPersistence(tmp_path), Settings())
    saved = []
    def handler(request):
        if request.method == "POST":
            assert request.url.path.endswith("/rpc/persist_paper_cycle")
            saved.append(json.loads(request.content)["payload"])
            return httpx.Response(200, json={})
        assert request.url.params["select"] == "report->research_registry"
        assert request.url.params["report->research_registry"] == "not.is.null"
        return httpx.Response(200, json=[{"research_registry": saved[-1]["research_registry"]}])
    store = SupabasePersistence("https://fixture.invalid", "fixture-not-a-key",
                                transport=httpx.MockTransport(handler))
    store.save({**report(), "research_registry": checkpoint}, None)
    assert load_registry(store) == checkpoint
    assert validate(checkpoint).champions == checkpoint["champions"]


def test_remote_error_no_local_fallback():
    store = SupabasePersistence("https://fixture.invalid", "fixture-not-a-key",
        transport=httpx.MockTransport(lambda request: httpx.Response(503)))
    with pytest.raises(PersistenceError):
        capture_registry(report(), store, Settings())


@pytest.mark.parametrize("body", [{}, [None], [{"research_registry": None}], [1, 2]])
def test_remote_invalid_checkpoint_is_loud(body):
    store = SupabasePersistence("https://fixture.invalid", "fixture-not-a-key",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body)))
    with pytest.raises((ValueError, KeyError, TypeError)):
        load_registry(store)


def test_paper_only_and_no_secret_snapshot(tmp_path):
    with pytest.raises(ValueError):
        capture_registry({**report(), "paper_only": False}, LocalPersistence(tmp_path), Settings())
    state = capture_registry(report(), LocalPersistence(tmp_path),
                             Settings(supabase_service_key="fixture-secret"))
    assert "fixture-secret" not in json.dumps(state)


def test_learning_defaults_and_env_bounds(monkeypatch):
    for name in ("LEARNING_FOLLOWUP_BUDGET", "LEARNING_BACKLOG_CAP", "LEARNING_GRACE_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    assert config() == {"budget": 10, "backlog": 500, "grace_seconds": 3600}
    monkeypatch.setenv("LEARNING_FOLLOWUP_BUDGET", "20")
    monkeypatch.setenv("LEARNING_BACKLOG_CAP", "1000")
    assert config()["budget"] == 20
    assert config()["backlog"] == 1000
    monkeypatch.setenv("LEARNING_FOLLOWUP_BUDGET", "21")
    with pytest.raises(ValueError):
        config()
    monkeypatch.setenv("LEARNING_FOLLOWUP_BUDGET", "-1")
    with pytest.raises(ValueError):
        config()
    monkeypatch.setenv("LEARNING_FOLLOWUP_BUDGET", "10")
    monkeypatch.setenv("LEARNING_BACKLOG_CAP", "1001")
    with pytest.raises(ValueError):
        config()


def news(at=MID, **values):
    return {"title": "FIXTURE news", "verification": "unverified",
            "sources": [{"fetched_at": at}], **values}


def test_holding_window_no_lookahead_no_imputed_outcome(tmp_path):
    store = LocalPersistence(tmp_path)
    old = {"cycle_news": {"items": [news(), news(AT), news(END),
            news("2026-11-17T23:59:59+00:00"),
            news(sources=[{"fetched_at": MID}, {"fetched_at": END}])]}}
    store.save({**report(), "research_news": old}, None)
    trade = {"id": "t", "token": "FIXTURE", "opened_at": AT, "closed_at": END,
             "measured_outcome": None}
    payload = {**report(), "paper_trades": [trade], "research_news": old}
    original = deepcopy(payload)
    result = holding_news(payload, store)
    row = result["records"][0]
    assert row["relevant"]["n"] == 2
    assert all(item["verification"] == "unverified" for item in row["relevant"]["items"])
    assert row["ledger_eligible"] is False
    assert result["status"] == "partial_snapshot_coverage"
    assert payload == original
    assert trade["measured_outcome"] is None


def test_holding_missing_and_empty_are_explicit(tmp_path):
    assert holding_news(report(), SimpleNamespace())["status"] == "no_closed_trades"
    r = {**report(), "paper_trades": [{"id": "t"}]}
    result = holding_news(r, LocalPersistence(tmp_path))
    assert result["records"][0]["status"] == "insufficient_data"
    r["paper_trades"] = [{"id": "t", "opened_at": AT, "closed_at": END}]
    result = holding_news(r, LocalPersistence(tmp_path))
    assert result["records"][0]["market_context"]["n"] == 0
    assert "absence is not proof" in result["coverage"]


def test_holding_refuses_nonpaper(tmp_path):
    with pytest.raises(ValueError):
        holding_news({"paper_only": False}, LocalPersistence(tmp_path))
