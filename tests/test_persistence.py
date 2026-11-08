"""Offline persistence contract tests. No network or real credentials."""
import asyncio
import json

import httpx
import pytest
from pydantic import SecretStr

from act.config.settings import Settings
from act.domain.models import TradeProposal
from act.orchestrator.cycle import run_cycle
from act.paper.ledger import PaperLedger
from act.persistence import (LocalPersistence, PersistenceError, SupabasePersistence,
                             get_persistence, ledger_snapshot, restore_ledger)


def settings(tmp_path, **kwargs):
    return Settings(_env_file=None, ledger_path=tmp_path / 'ledger.json',
                    report_dir=tmp_path / 'reports', http_retries=0, **kwargs)


def remote(handler):
    return SupabasePersistence('https://example.invalid', 'test-only-not-a-key',
                               transport=httpx.MockTransport(handler))


def test_headers_and_atomic_rpc_payload():
    seen = []
    def handler(request):
        seen.append(request)
        assert request.headers['apikey'] == 'test-only-not-a-key'
        assert request.headers['authorization'] == 'Bearer test-only-not-a-key'
        return httpx.Response(204)
    report = {'paper_only': True, 'cycle_id': 'test', 'markets': [],
              'discoveries': [], 'assessments': {}, 'status': 'failed'}
    ledger = {'history': [], 'account': {}, 'positions': [], 'trades': []}
    remote(handler).save(report, ledger)
    assert len(seen) == 1
    assert seen[0].method == 'POST'
    assert seen[0].url.path == '/rest/v1/rpc/persist_paper_cycle'
    assert json.loads(seen[0].content) == {'payload': report, 'ledger': ledger}


def test_configuration_is_offline_unless_both_values_set(tmp_path):
    config = settings(tmp_path)
    assert isinstance(get_persistence(config), LocalPersistence)
    partial = config.model_copy(update={'supabase_url': 'https://example.invalid'})
    with pytest.raises(ValueError, match='Set both'):
        get_persistence(partial)
    both = partial.model_copy(update={'supabase_service_key': SecretStr('test')})
    assert isinstance(get_persistence(both), SupabasePersistence)


@pytest.mark.parametrize('code', [401, 403, 429, 500])
def test_http_failure_is_explicit_and_redacted(code):
    store = remote(lambda request: httpx.Response(code, text='sensitive provider body'))
    with pytest.raises(PersistenceError) as error:
        store.save({'paper_only': True}, None)
    assert str(error.value) == f'Supabase request failed (HTTP {code})'
    assert 'example.invalid' not in str(error.value)
    assert 'sensitive' not in str(error.value)


def test_transport_failure_redacted():
    def handler(request):
        raise httpx.ConnectError('private endpoint and key', request=request)
    with pytest.raises(PersistenceError, match='^Supabase transport failure$'):
        remote(handler).load_history()


@pytest.mark.parametrize('body', [{}, [{'history': []}], [{'history': 'bad'}]])
def test_corrupt_remote_state_is_not_treated_as_new_account(body):
    with pytest.raises(PersistenceError, match='Invalid persisted'):
        remote(lambda request: httpx.Response(200, json=body)).load_history()


def test_empty_remote_account():
    assert remote(lambda request: httpx.Response(200, json=[])).load_history() is None


def test_ledger_restores_positions_trades_and_cash_after_disk_loss(tmp_path):
    config = settings(tmp_path)
    ledger = PaperLedger(config.ledger_path)
    proposal = TradeProposal(token='SOL', entry_price=100, size=2, stop_loss=90,
                             reason='offline test')
    first = ledger.open(proposal)
    ledger.close(first.id, 110, 'offline test exit')
    second = ledger.open(proposal)
    account = ledger.account_state({'SOL': 105}).model_dump(mode='json')
    snapshot = ledger_snapshot(ledger, account)
    config.ledger_path.unlink()
    def handler(request):
        assert request.method == 'GET'
        assert request.url.params['id'] == 'eq.paper-main'
        return httpx.Response(200, json=[{'history': snapshot['history']}])
    restored = restore_ledger(remote(handler), config)
    assert restored.history == snapshot['history']
    assert restored.cash == ledger.cash
    assert list(restored.positions) == [second.id]
    assert restored.closed_trades == ledger.closed_trades


def test_failed_collector_cycle_is_still_persisted(tmp_path):
    written = []
    def handler(request):
        if request.method == 'GET':
            return httpx.Response(200, json=[])
        written.append(json.loads(request.content))
        return httpx.Response(204)
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(503))) as client:
            with pytest.raises(RuntimeError, match='Cycle failed'):
                await run_cycle(settings(tmp_path), client=client, persistence=remote(handler))
    asyncio.run(exercise())
    assert len(written) == 1
    assert written[0]['payload']['status'] == 'failed'
    assert written[0]['payload']['collectors']['CoinGecko']['status'] == 'failed'
    assert written[0]['ledger']['account']['equity'] == 10000
    assert written[0]['ledger']['positions'] == []


def test_refuses_non_paper_reports():
    store = remote(lambda request: pytest.fail('Must not perform HTTP'))
    with pytest.raises(ValueError, match='Paper-only'):
        store.save({'paper_only': False}, None)


def test_local_report_and_existing_json_ledger(tmp_path):
    config = settings(tmp_path)
    store = get_persistence(config)
    ledger = restore_ledger(store, config)
    account = ledger.account_state({}).model_dump(mode='json')
    report = {'cycle_id': 'offline', 'paper_only': True}
    store.save(report, ledger_snapshot(ledger, account))
    assert json.loads((config.report_dir / 'offline.json').read_text()) == report
    assert PaperLedger(config.ledger_path).history == ledger.history
