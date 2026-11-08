"""Paper-only persistence; remote failures never silently fall back to disk.

A single Render worker owns the ledger. The RPC is atomic and cycle-ID idempotent.
No Supabase SDK or live-trading credentials are used.
"""
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Protocol
from urllib.parse import urlsplit

import httpx

from act.paper.ledger import PaperLedger


class PersistenceError(RuntimeError):
    pass


class Persistence(Protocol):
    def load_history(self) -> list | None: ...
    def save(self, report: dict, ledger: dict | None) -> None: ...


class LocalPersistence:
    def __init__(self, directory):
        self.directory = Path(directory)

    def load_history(self):
        # PaperLedger already loads and validates the configured local JSON file.
        return None

    def save(self, report, ledger):
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / (report['cycle_id'] + '.json')
        path.write_text(json.dumps(report, indent=2, allow_nan=False), encoding='utf-8')


class SupabasePersistence:
    def __init__(self, url, key, timeout=15, transport=None):
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.query or parsed.fragment:
            raise ValueError('SUPABASE_URL must be a plain HTTPS project URL')
        if parsed.username or parsed.password or parsed.path.rstrip('/'):
            raise ValueError('SUPABASE_URL must not contain credentials or a path')
        self._url = url.rstrip('/') + '/rest/v1/'
        self._headers = {'apikey': key, 'Authorization': 'Bearer ' + key}
        self._timeout = timeout
        self._transport = transport

    def _request(self, method, path, **kwargs):
        # Do not log httpx exceptions: their request URL is private configuration.
        try:
            with httpx.Client(timeout=self._timeout, transport=self._transport) as client:
                response = client.request(method, self._url + path,
                                          headers=self._headers, **kwargs)
        except httpx.HTTPError:
            raise PersistenceError('Supabase transport failure') from None
        if not response.is_success:
            raise PersistenceError(f'Supabase request failed (HTTP {response.status_code})')
        return response

    def load_history(self):
        response = self._request('GET', 'paper_ledger_state', params={
            'id': 'eq.paper-main', 'select': 'history', 'limit': '1'})
        try:
            rows = response.json()
            if not isinstance(rows, list) or len(rows) > 1:
                raise ValueError
            if not rows:
                return None
            history = rows[0]['history']
            if not isinstance(history, list) or not history:
                raise ValueError
            return history
        except (ValueError, KeyError, TypeError):
            raise PersistenceError('Invalid persisted paper ledger response') from None

    def save(self, report, ledger):
        if report.get('paper_only') is not True:
            raise ValueError('Paper-only reports required')
        self._request('POST', 'rpc/persist_paper_cycle', json={
            'payload': report, 'ledger': ledger})


def get_persistence(settings) -> Persistence:
    key = settings.supabase_service_key.get_secret_value()
    if bool(settings.supabase_url) != bool(key):
        raise ValueError('Set both SUPABASE_URL and SUPABASE_SERVICE_KEY, or neither')
    if settings.supabase_url:
        return SupabasePersistence(settings.supabase_url, key, settings.http_timeout)
    return LocalPersistence(settings.report_dir)


def restore_ledger(store, settings):
    history = store.load_history()
    if history is not None:
        path = settings.ledger_path
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            try:
                json.dump(history, handle, allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise
        try:
            # Validate before replacing the local file; never reset a corrupt ledger.
            PaperLedger(temporary, fee_pct=settings.fee_pct,
                        slippage_pct=settings.slippage_pct)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return PaperLedger(settings.ledger_path, fee_pct=settings.fee_pct,
                       slippage_pct=settings.slippage_pct)


def ledger_snapshot(ledger, account):
    return {'history': ledger.history, 'account': account,
            'positions': [p.model_dump(mode='json') for p in ledger.positions.values()],
            'trades': [t.model_dump(mode='json') for t in ledger.closed_trades]}
