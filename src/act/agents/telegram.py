"""Optional bounded brief push. Token is read only from environment; never logged."""
import asyncio
import logging
import os

import httpx

LOG = logging.getLogger(__name__)
_DISABLED_LOGGED = False


async def push_brief(brief, *, client=None):
    global _DISABLED_LOGGED
    token = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
    if not token:
        if not _DISABLED_LOGGED:
            LOG.info('TELEGRAM_DISABLED: TELEGRAM_BOT_TOKEN is unset')
            _DISABLED_LOGGED = True
        return {'status': 'disabled'}
    chat = os.getenv('TELEGRAM_CHAT_ID', '').strip()
    if not chat:
        LOG.error('TELEGRAM_FAILED: TELEGRAM_CHAT_ID is required when token is set')
        return {'status': 'failed', 'error': 'Missing TELEGRAM_CHAT_ID'}
    lines = ['Paper research news | as of ' + str(brief.get('as_of') or 'unavailable'),
             f"Verified {brief.get('verified', 0)} / total {brief.get('total', 0)}"]
    for item in brief.get('top_verified', [])[:5]:
        lines.append(item['title'][:300])
        lines.extend(source['url'][:350] for source in item.get('sources', [])[:2])
    if not brief.get('top_verified'):
        lines.append('No verified items available. No substitute headlines.')
    payload = {'chat_id': chat, 'text': '\n'.join(lines)[:4000],
               'disable_web_page_preview': True}
    # httpx INFO logs include URL paths; never place a secret token in such a path.
    # Telegram supports the token in the URL only. Suppress library URL logging
    # within this coroutine; transport failures are replaced before traceback logging.
    http_log = logging.getLogger('httpx')
    core_log = logging.getLogger('httpcore')
    old_http, old_core = http_log.disabled, core_log.disabled
    http_log.disabled = core_log.disabled = True
    try:
        try:
            async with httpx.AsyncClient(timeout=5) as owned:
                transport = client if client is not None else owned
                response = await asyncio.wait_for(transport.post(
                    'https://api.telegram.org/bot' + token + '/sendMessage',
                    json=payload, timeout=5), timeout=6)
                if not response.is_success or response.json().get('ok') is not True:
                    raise ValueError('Telegram rejected push')
        except Exception:
            # Do not retain/log request exception text or response bodies containing secrets.
            raise RuntimeError('Telegram push failed; no automatic retry') from None
    except RuntimeError:
        LOG.exception('TELEGRAM_FAILED')
        return {'status': 'failed', 'error': 'Telegram push failed; no automatic retry'}
    finally:
        http_log.disabled, core_log.disabled = old_http, old_core
    return {'status': 'sent'}
