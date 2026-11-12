"""Presentation only: pure, escaped HUD views over existing stored cycle records."""
import math
import re
from datetime import datetime
from html import escape


def text(value):
    return escape(str(value)) if value is not None else 'n/a'


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def money(value):
    return f'${value:,.2f}' if number(value) else 'Unavailable'


def timestamp(value):
    try:
        at = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return at.timestamp() if at.tzinfo is not None else None
    except (TypeError, ValueError, AttributeError, OverflowError):
        return None


def chart(reports, logarithmic=False, events=()):
    """Elapsed-time x axis; >40min is an observation gap, not proof of sleep."""
    points = []
    invalid_times = 0
    for report in reports:
        p = report.get('equity_snapshot') or {}
        if not p:
            continue
        at = timestamp(p.get('timestamp'))
        if at is None:
            invalid_times += 1
            continue
        points.append((at, p))
    points.sort(key=lambda item: item[0])
    valid = [p['equity'] for _, p in points if number(p.get('equity'))]
    if not valid:
        return '<p>No equity snapshots available. No fabricated history.</p>'
    if logarithmic and any(v <= 0 for v in valid):
        return '<p>Log scale unavailable: nonpositive equity recorded. Use linear view.</p>'
    transform = math.log10 if logarithmic else float
    low, high = min(valid), max(valid)
    bottom, top = transform(low), transform(high)
    padding = (top - bottom) * .12 or max(abs(top) * .005, .01)
    bottom, top = bottom - padding, top + padding
    start, end = points[0][0], points[-1][0]

    def x(at):
        return 85 + 660 * (at - start) / (end - start) if end != start else 415

    def y(value):
        return 220 - 170 * (transform(value) - bottom) / (top - bottom)

    output, segments, segment = [], [], []
    previous = None
    gaps = 0
    for at, p in points:
        value = p.get('equity')
        separated = previous is not None and (at - previous > 2400 or at == previous)
        if separated or not number(value):
            if segment:
                segments.append(segment)
            segment = []
            gaps += 1
            output.append(f'<path class="gap-marker" d="M{x(at):.2f} 45V225" '
                          'stroke="#ff667d" stroke-dasharray="3 6"><title>'
                          'Observation gap / unavailable valuation; no interpolation</title></path>')
        if number(value):
            px, py = x(at), y(value)
            segment.append(f'{px:.2f},{py:.2f}')
            output.append(f'<circle cx="{px:.2f}" cy="{py:.2f}" r="3" fill="#5fffd2">'
                          f'<title>{text(p.get("timestamp"))}: USD {text(value)}</title></circle>')
        previous = at
    if segment:
        segments.append(segment)
    for s in segments:
        output.append('<polyline points="' + ' '.join(s) + '" fill="none" '
                      'stroke="#f4f7f6" stroke-width="2"/>')
    for i in range(4):
        py = 50 + i * 170 / 3
        v = top - i * (top - bottom) / 3
        v = 10 ** v if logarithmic else v
        output.append(f'<path d="M85 {py:.2f}H745" stroke="#303638" stroke-dasharray="3 6"/>'
                      f'<text x="2" y="{py:.2f}" fill="#a7b1b1" font-size="12">${v:,.0f}</text>')
    for index, (at, label) in enumerate(events):
        when = timestamp(at)
        if when is None or not start <= when <= end:
            continue
        output.append(f'<path d="M{x(when):.2f} 40V225" stroke="#5fffd2" stroke-dasharray="2 5"/>'
                      f'<text x="90" y="{15 + index * 15}" fill="#a7b1b1" font-size="11">'
                      f'{text(at)} · {text(label)}</text>')
    return (f'<p>Observed equity range USD {text(low)} – {text(high)}. {len(valid)} valued snapshots; '
            f'{len(points) - len(valid)} unavailable (gaps). {gaps} gap markers; '
            f'{invalid_times} invalid timestamps omitted.</p>'
            '<svg viewBox="0 0 800 265" role="img" aria-label="Paper equity over time" '
            'style="width:100%;background:#080b0c">' + ''.join(output)
            + f'<text x="85" y="250" fill="#a7b1b1" font-size="11">{text(points[0][1]["timestamp"])}</text>'
            + f'<text x="745" y="265" text-anchor="end" fill="#a7b1b1" font-size="11">{text(points[-1][1]["timestamp"])}</text></svg>'
            '<p>UTC · latest 30 persisted cycles only; earlier snapshots retained. Snapshots begin with '
            'capture; no synthetic backfill. One observation is a point. Gaps over 40 minutes '
            '(twice nominal cadence), duplicate times and missing valuations break the line; '
            'gaps may include sleep, not verified uptime.</p>')


def recorded_events(report):
    rows = (report.get('paper_trades') or []) + (report.get('paper_positions') or [])
    opened = [r for r in rows if timestamp(r.get('opened_at')) is not None]
    events = []
    if opened:
        first = min(opened, key=lambda r: timestamp(r['opened_at']))
        events.append((first['opened_at'], 'First retained paper BUY · ' + str(first.get('token', 'n/a'))))
    for row in report.get('paper_trades') or []:
        if row.get('exit_reason') == 'stop_loss' and timestamp(row.get('closed_at')) is not None:
            events.append((row['closed_at'], 'stop_loss · ' + money(row.get('realized_pnl'))))
    return events


def raw(value):
    import json
    return '<pre>' + text(json.dumps(value, indent=2)) + '</pre>'


def budget_bar(used, cap):
    if not number(used) or not number(cap) or cap <= 0 or used < 0:
        return '<p>Budget ratio: n/a</p>'
    return (f'<p>Budget used / cap: {text(used)} / {text(cap)}</p>'
            f'<meter min="0" max="{cap}" value="{min(used, cap)}">{used} / {cap}</meter>')


def hero(report, reports, error=None):
    account = report.get('paper_account') or {}
    equity = account.get('equity')
    # Paper starting balance is the established ledger configuration, not a sampled metric.
    pnl = equity - 10000 if number(equity) else None
    tone = 'negative' if number(pnl) and pnl < 0 else 'positive' if number(pnl) and pnl > 0 else ''
    pct = f'{pnl / 10000 * 100:+.1f}%' if number(pnl) else 'insufficient data'
    learning = report.get('learning') or {}
    stats = learning.get('stats') or {}
    readiness = report.get('readiness') or {}
    metrics = readiness.get('metrics') or {}
    cycles = (readiness.get('provenance') or {}).get('persisted_cycle_ids')
    trades = report.get('paper_trades')
    n = len(trades) if isinstance(trades, list) else None
    known = n and all(number(t.get('realized_pnl')) for t in trades)
    win = f'{100 * sum(t["realized_pnl"] > 0 for t in trades) / n:.1f}%' if known else 'n/a'
    candidates = len(report['decisions']) if isinstance(report.get('decisions'), list) else None
    cumulative = sum(len(r['decisions']) for r in reports) if reports and not error and all(isinstance(r.get('decisions'), list) for r in reports) else None
    fields = [('Cycles (retained + current)', len(cycles) + 1 if cycles is not None else None),
              ('Candidates · latest', candidates), ('Candidates · loaded window only', cumulative),
              ('Closed trades', n), ('Win rate', win),
              ('Open positions', len(report['paper_positions']) if isinstance(report.get('paper_positions'), list) else None),
              ('Learning sessions · tracked ever', stats.get('tracked_ever')),
              ('Follow-up horizons · pending', stats.get('pending')),
              ('Follow-up horizons · terminal in view', stats.get('terminal_in_view'))]
    out = ('<header class="hud-header"><h1>AUTONOMOUS CRYPTO TRADER</h1>'
           '<span class="mode">● PAPER MODE</span></header>'
           '<p>SIMULATED - PAPER TRADING ONLY · READ-ONLY RESEARCH · NO REAL-MONEY CAPABILITY</p>'
           '<div class="cards"><article><h2>EQUITY</h2><strong>' + money(equity)
           + '</strong><small>initial: $10,000.00 · paper starting balance</small></article>'
           f'<article><h2>TOTAL P&amp;L</h2><strong class="{tone}">{money(pnl)}</strong>'
           f'<small class="{tone}">{pct} · realized + unrealized vs initial</small></article>'
           '<article><h2>RUNNING COST</h2><strong>$0.00<small>/mo</small></strong>'
           '<small>free tier · configured hosting, not a billing meter</small></article></div>'
           '<section class="history"><h2>// BALANCE HISTORY (LOG)</h2>'
           + chart(reports, True, recorded_events(report))
           + '<details><summary>Linear scale / exact observations available below</summary>'
           + chart(reports, False, recorded_events(report)) + '</details></section>'
           '<section><h2>// LEDGER + LEARNING</h2><div class="stats">')
    out += ''.join('<div><small>' + text(k) + '</small><b>' + text(v) + '</b></div>' for k, v in fields)
    out += ('</div><p class="negative">SMALL-N: win rate n=' + text(n)
            + '; descriptive only, not validation. Terminal horizons include missed/unavailable outcomes, '
            'not successful observations. Cumulative candidates cover only the loaded window, not lifetime.</p>'
            + budget_bar(stats.get('budget_used'), (learning.get('config') or {}).get('budget'))
            + '<details><summary>Recorded learning status counts / win-rate provenance</summary>'
            + raw(stats.get('status_counts')) + raw(metrics.get('win_rate_pct')) + '</details></section>')
    collectors = report.get('collectors') or {}
    security = report.get('security_source_stats') or {}
    sources = [('01', 'GeckoTerminal', {'collector': collectors.get('geckoterminal'), 'discovery': report.get('discovery_stats'), 'OHLCV / TA': report.get('intelligence_stats'), 'volume_profile': report.get('volume_profile_counts')}),
               ('02', 'DexScreener', {'collector': collectors.get('dexscreener'), 'discovery': report.get('discovery_stats')}),
               ('03', 'Solana RPC / on-chain + RugCheck fallback', {'security_source_stats': report.get('security_source_stats'), 'stale_tokens': (report.get('position_management') or {}).get('stale_tokens')})]
    out += '<div class="sources">'
    for index, name, data in sources:
        out += '<article><small>' + index + ' // DATA SOURCE</small><h2>' + name + '</h2>'
        if index == '03':
            out += budget_bar(security.get('rpc_calls'), security.get('budget'))
        else:
            out += '<p>Coverage / health: recorded evidence below; no inferred availability score.</p>'
        out += '<details><summary>Latest recorded coverage / health</summary>' + raw(data) + '</details></article>'
    return out + '</div><p>429 events: n/a unless explicitly recorded below. Source errors are not retry counts.</p>'


CSS = '''
:root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;background:#080b0c;color:#f4f7f6;font:13px/1.6 ui-monospace,SFMono-Regular,Consolas,monospace}
main{max-width:1180px;margin:auto;padding:16px}h1,h2,h3{line-height:1.35;letter-spacing:.05em}h1{font-size:clamp(18px,3vw,30px)}h2{font-size:14px;text-transform:uppercase;color:#b6c0c0}p,small{color:#a7b1b1}small{display:block;font-size:11px}.hud-header{display:flex;gap:16px;align-items:center;justify-content:space-between;border-bottom:1px solid #303638;flex-wrap:wrap}.mode,.positive{color:#5fffd2!important}.negative,[role=alert]{color:#ff667d!important}.cards,.sources{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}article,section,.view{border:1px solid #303638;background:#080b0c;border-radius:0;padding:14px;margin:14px 0}article{min-width:0;margin:0}article strong{display:block;font-size:clamp(18px,3.8vw,38px);line-height:1.3;letter-spacing:-.05em;overflow-wrap:anywhere}article strong small{display:inline;font-size:12px}article h2{font-size:11px}.stats{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.stats b{font-size:22px}.scroll{overflow-x:auto}table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:10px;border-bottom:1px dashed #303638;overflow-wrap:anywhere}th{color:#a7b1b1;font-size:11px;text-transform:uppercase}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}a{color:#5fffd2}details{border-bottom:1px solid #303638}summary{cursor:pointer;padding:12px 0;min-height:44px}summary:focus-visible,a:focus-visible{outline:2px solid #5fffd2;outline-offset:3px}.view>summary{text-transform:uppercase;letter-spacing:.05em;font-weight:bold}.view>section{border:0;padding:0;margin:0}.view{scroll-margin-top:12px}meter{width:100%;height:6px;accent-color:#5fffd2}svg{max-height:none}.sources h2{font-size:13px}.history{overflow:hidden}nav{position:static;background:#080b0c;padding:12px 0}nav a{display:inline-block;margin:4px 10px 4px 0}
@media(min-width:760px){main{padding:28px}.stats{grid-template-columns:repeat(5,minmax(0,1fr))}}
@media(max-width:460px){main{padding:10px}.cards{gap:5px}.cards article{padding:8px}.cards h2{font-size:10px}.cards small{font-size:10px}.sources{grid-template-columns:1fr}.history{padding:10px}}
'''


def restyle(page, report, reports, error=None):
    """Wrap trusted server-rendered sections; their escaped contents remain byte-for-byte."""
    page = page.replace('</style>', CSS + '</style>', 1)
    # These sections are generated by our renderers, never by user-supplied HTML.
    def accordion(match):
        identifier, contents = match.groups()
        heading = re.search(r'<h2>(.*?)</h2>', contents, re.S)
        label = heading.group(1) if heading else text(identifier)
        return (f'<details class="view" id="{identifier}" open><summary>// {label}</summary>'
                + '<section>' + contents + '</section></details>')
    page = re.sub(r'<section id="([a-z-]+)">(.*?)</section>', accordion, page, flags=re.S)
    page = re.sub(r'<nav>.*?</nav>', '', page, flags=re.S)
    page = page.replace('<main>', '<main>' + hero(report, reports, error)
                        + '<h2>// ALL RESEARCH VIEWS · EXPAND / COLLAPSE</h2>', 1)
    return page
