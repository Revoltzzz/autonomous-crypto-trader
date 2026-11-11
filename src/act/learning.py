"""Evidence-only labeled sessions. Never imported by strategy or executor.

The last durable cycle carries a compact checkpoint; all earlier event JSON remains
immutable in cycles.report. No pre-release history is backfilled.
"""
import asyncio
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import logging
import math
import os
from statistics import median
from time import perf_counter
from urllib.parse import quote

import httpx

HORIZONS = {"1h": 3600, "6h": 21600, "24h": 86400, "72h": 259200, "7d": 604800}
LOG = logging.getLogger(__name__)


def stamp():
    return datetime.now(timezone.utc).isoformat()


def date(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Learning timestamps require timezone")
    return result


def number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) and result >= 0 else None
    except (ValueError, TypeError):
        return None


def config():
    result = {}
    for key, default, low, high in (("budget", 6, 0, 20), ("backlog", 200, 1, 1000),
                                  ("grace_seconds", 3600, 0, 86400)):
        name = {"budget": "LEARNING_FOLLOWUP_BUDGET", "backlog": "LEARNING_BACKLOG_CAP",
                "grace_seconds": "LEARNING_GRACE_SECONDS"}[key]
        value = int(os.environ.get(name, default))
        if not low <= value <= high:
            raise ValueError(f"{name} outside collection bounds [{low}, {high}]")
        result[key] = value
    return result


def load_checkpoint(store):
    if hasattr(store, "_request"):
        rows = store._request("GET", "cycles", params={"select": "report->learning",
                             "order": "completed_at.desc", "limit": "1", "report->learning": "not.is.null"}).json()
        if not isinstance(rows, list):
            raise ValueError("Invalid learning checkpoint response")
        return rows[0].get("learning") if rows else None
    paths = sorted(store.directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in paths:
        learning = json.loads(path.read_text()).get("learning")
        if learning is not None:
            return learning
    return None


def initial(at):
    return {"version": 1, "started_at": at, "seen": {}, "active": [], "recent": []}


def event(session, horizon, status, at, **values):
    due = date(session["at"]) + timedelta(seconds=HORIZONS[horizon])
    return {"session_id": session["decision_id"], "mint_address": session["mint_address"],
            "pool_address": session["pool_address"], "decision_cycle_id": session["cycle_id"],
            "horizon": horizon, "due_at": due.isoformat(), "observed_at": at,
            "lateness_seconds": max(0, (date(at) - due).total_seconds()),
            "status": status, "return_pct": None, "liquidity_change_pct": None,
            "price_usd": None, "liquidity_usd": None, "volume_24h_usd": None,
            "pool_exists": None, "liquidity_drained": None, **values}


def register(state, report, at):
    pools = {p["mint_address"]: p for p in report.get("discoveries", [])}
    for decision in report.get("decisions", []):
        mint = decision["mint_address"]
        if mint not in state["seen"]:
            pool = pools.get(mint, {})
            sid = decision["id"]
            state["seen"][mint] = sid
            state["active"].append({"decision_id": sid, "cycle_id": report["cycle_id"],
                "mint_address": mint, "pool_address": decision.get("pool_address"),
                "symbol": decision.get("symbol"), "at": decision.get("created_at", at),
                "verdict": decision["decision"], "reasons": decision.get("failure_reasons", []),
                "price_usd": pool.get("price_usd"), "liquidity_usd": pool.get("liquidity_usd"),
                "quote_at": pool.get("fetched_at") or pool.get("discovered_at"),
                "quote_time_status": "provider quote timestamp not retained by discovery; null is unknown",
                "baseline_capture_at": at, "discovery_cycle_started_at": report.get("started_at"),
                "quote_source": pool.get("source_url"),
                "observations": {}})
        # Reference original immutable decision + discovery in its cycle, not a replacement snapshot.
        decision["learning_session_id"] = state["seen"][mint]


def prepare(state, at, cap, grace):
    events = []
    state["active"].sort(key=lambda s: (s["at"], s["decision_id"]))
    removed = state["active"][:-cap] if len(state["active"]) > cap else []
    for session in removed:
        for horizon in HORIZONS:
            if horizon not in session["observations"]:
                row = event(session, horizon, "missed_backlog_cap", at)
                session["observations"][horizon] = row
                events.append(row)
        state["recent"].append(session)
    state["active"] = state["active"][len(removed):]
    pending = []
    for session in state["active"]:
        for horizon, seconds in HORIZONS.items():
            if horizon in session["observations"]:
                continue
            due = date(session["at"]) + timedelta(seconds=seconds)
            if date(at) > due + timedelta(seconds=grace):
                row = event(session, horizon, "missed_service_gap_or_budget", at)
                session["observations"][horizon] = row
                events.append(row)
            elif date(at) >= due:
                pending.append((due, session["decision_id"], horizon, session))
    return sorted(pending, key=lambda item: item[:3]), events


async def quote_source(client, source, session):
    pool = session.get("pool_address")
    mint = session["mint_address"]
    if not pool:
        return {"status": "data_missing", "error": "No decision-time pool address"}
    url = ("https://api.geckoterminal.com/api/v2/networks/solana/pools/" + quote(pool, safe="")
           if source == "geckoterminal" else
           "https://api.dexscreener.com/token-pairs/v1/solana/" + quote(mint, safe=""))
    provenance = {"source": source, "source_url": url, "params": {}, "requested_at": stamp()}
    try:
        response = await client.get(url, timeout=5)
        provenance.update(http_status=response.status_code, fetched_at=stamp())
        if response.status_code == 429:
            return {**provenance, "status": "rate_limited"}
        if response.status_code == 404 and source == "geckoterminal":
            return {**provenance, "status": "pool_not_found", "pool_exists": False,
                    "availability_scope": "provider index only; not proof of onchain death"}
        response.raise_for_status()
        payload = response.json()
        if source == "geckoterminal":
            data = payload["data"]
            attrs = data["attributes"]
            base = data["relationships"]["base_token"]["data"]["id"]
            if attrs["address"] != pool or base != "solana_" + mint:
                raise ValueError("Pool/base identity mismatch; no comparable USD price")
            price, liquidity, volume = (attrs.get("base_token_price_usd"),
                attrs.get("reserve_in_usd"), (attrs.get("volume_usd") or {}).get("h24"))
        else:
            if not isinstance(payload, list):
                raise ValueError("Expected pair list")
            pair = next((p for p in payload if p.get("chainId") == "solana"
                         and p.get("pairAddress") == pool
                         and (p.get("baseToken") or {}).get("address") == mint), None)
            if pair is None:
                return {**provenance, "status": "pool_not_found", "pool_exists": False,
                        "availability_scope": "provider index only; not proof of onchain death"}
            price, liquidity, volume = (pair.get("priceUsd"),
                (pair.get("liquidity") or {}).get("usd"), (pair.get("volume") or {}).get("h24"))
        values = {"price_usd": number(price), "liquidity_usd": number(liquidity),
                  "volume_24h_usd": number(volume)}
        return {**provenance, **values, "status": "observed" if all(v is not None for v in values.values())
                else "partial", "pool_exists": True,
                "liquidity_drained": values["liquidity_usd"] == 0 if values["liquidity_usd"] is not None else None}
    except Exception as exc:
        return {**provenance, "status": "data_missing", "error": str(exc)}


def summary(values, total=None):
    valid = [float(v) for v in values if type(v) in (int, float) and math.isfinite(v)]
    return {"n": len(valid), "missing": (len(values) if total is None else total) - len(valid),
            "min": min(valid) if valid else None, "median": median(valid) if valid else None,
            "max": max(valid) if valid else None}


def distributions(report, events):
    decisions = report.get("decisions", [])
    from act.intelligence import WINDOWS

    ta = {key: [] for key in WINDOWS}
    security, missing, reasons = defaultdict(Counter), Counter(), Counter()
    market = {key: [] for key in ("price_usd", "liquidity_usd", "volume_24h_usd", "buys_24h", "sells_24h")}
    pools = {p["mint_address"]: p for p in report.get("discoveries", [])}
    lp = []
    for d in decisions:
        proposal = d.get("proposal") or {}
        indicators = ((proposal.get("intelligence") or {}).get("ta") or {}).get("indicators") or {}
        for key in ta:
            value = indicators.get(key) or {}
            ta[key].append(value.get("value") if value.get("available") else None)
        for key in market:
            market[key].append(pools.get(d["mint_address"], {}).get(key))
        assessment = report.get("assessments", {}).get(d["mint_address"], {})
        lp.append(assessment.get("lp_locked_pct"))
        security["lp_locked_pct:" + str(assessment.get("source", "missing"))][json.dumps(assessment.get("lp_locked_pct"))] += 1
        fields = (proposal.get("security_evidence") or {}).get("fields") or {}
        for key in ("mint_authority_revoked", "freeze_authority_revoked", "top_holder_concentration_pct"):
            field = fields.get(key) or {}
            value = field.get("effective_value")
            source = field.get("source", "missing")
            security[key + ":" + source][json.dumps(value, sort_keys=True)] += 1
            missing["security." + key] += int(value is None)
            for provider in ("rugcheck", "onchain"):
                observed = field.get(provider + "_value")
                security[key + ":raw_" + provider][json.dumps(observed, sort_keys=True)] += 1
        reasons.update(d.get("failure_reasons", []))
        for key in d.get("missing_fields", []):
            missing["decision." + key] += 1
    missing["security.lp_locked_pct"] = sum(v is None for v in lp)
    return {"candidate_n": len(decisions),
            "market": {k: summary(v) for k, v in market.items()},
            "lp_locked_pct": summary(lp), "ta": {k: summary(v, len(decisions)) for k, v in ta.items()},
            "security_values_by_source": {k: {"n": sum(v.values()), "values": dict(v)} for k, v in security.items()},
            "missing_fields": {k: {"n_missing": v, "n": len(decisions)} for k, v in missing.items()},
            "rejection_reasons": {k: {"n": v, "candidate_n": len(decisions)} for k, v in reasons.items()},
            "outcomes": {h: summary([e.get("return_pct") for e in events if e["horizon"] == h]) for h in HORIZONS}}


def calibration(sessions):
    groups = defaultdict(list)
    for session in sessions:
        for horizon, row in session["observations"].items():
            for reason in session["reasons"]:
                groups[(horizon, reason)].append(row.get("return_pct"))
    return [{"horizon": h, "reason": r, **summary(v)} for (h, r), v in sorted(groups.items())]


async def track(report, store, client=None, at=None, cfg=None):
    started = perf_counter()
    at, cfg = at or stamp(), cfg or config()
    previous = await asyncio.to_thread(load_checkpoint, store)
    state = deepcopy(previous["state"]) if previous else initial(at)
    if state.get("version") != 1:
        raise ValueError("Unsupported learning checkpoint version")
    register(state, report, at)
    pending, events = prepare(state, at, cfg["backlog"], cfg["grace_seconds"])
    used, blocked, source_counts = 0, set(), Counter()
    owned = client is None
    http = client or httpx.AsyncClient()
    try:
        for _, _, horizon, session in pending:
            if used >= cfg["budget"]:
                break
            attempts = []
            result = None
            for source in ("geckoterminal", "dexscreener"):
                if source in blocked or used >= cfg["budget"]:
                    continue
                used += 1
                source_counts[source] += 1
                result = await quote_source(http, source, session)
                attempts.append(result)
                if result["status"] == "rate_limited":
                    blocked.add(source)
                if result["status"] in ("observed", "partial", "pool_not_found"):
                    break
                LOG.error("LEARNING_SOURCE_FAILED %s", json.dumps(result))
            if result is None:
                break
            observed_at = at if client is not None else stamp()
            row = event(session, horizon, result["status"], observed_at, **{
                k: v for k, v in result.items() if k != "status"}, attempts=attempts)
            baseline = number(session.get("price_usd"))
            liquidity = number(session.get("liquidity_usd"))
            if baseline and row.get("price_usd") is not None:
                row["return_pct"] = (row["price_usd"] / baseline - 1) * 100
            if liquidity and row.get("liquidity_usd") is not None:
                row["liquidity_change_pct"] = (row["liquidity_usd"] / liquidity - 1) * 100
            session["observations"][horizon] = row
            events.append(row)
    finally:
        if owned:
            await http.aclose()
    active = []
    for session in state["active"]:
        if len(session["observations"]) == len(HORIZONS):
            state["recent"].append(session)
        else:
            active.append(session)
    state["active"] = active
    state["recent"] = state["recent"][-200:]
    sessions = state["recent"] + active
    counts = Counter(row["status"] for s in sessions for row in s["observations"].values())
    report["learning"] = {"state": state, "events": events, "config": cfg,
        "stats": {"pending": sum(len(HORIZONS) - len(s["observations"]) for s in active),
                  "terminal_in_view": sum(counts.values()), "status_counts": dict(counts),
                  "tracked_ever": len(state["seen"]), "active": len(active),
                  "budget_used": used, "source_calls": dict(source_counts),
                  "due_at_cycle_start": len(pending), "event_n": len(events),
                  "elapsed_ms": round((perf_counter() - started) * 1000, 3)},
        "distributions": distributions(report, events), "calibration": calibration(sessions)}
    LOG.info("LEARNING_STATS %s", json.dumps(report["learning"]["stats"], allow_nan=False))


def learning_panel(latest, reports):
    from act.results import raw, table, text

    learning = latest.get("learning")
    out = '<section id="learning"><h2>Learning / calibration (evidence only)</h2>'
    if not learning:
        return out + '<p>Outcome tracking not recorded for this cycle.</p>' + raw(latest.get("learning_error")) + '</section>'
    state = learning["state"]
    out += ('<p>Outcome tracking started ' + text(state["started_at"]) + '. No backfill.</p>'
            '<p>SMALL-N WARNING: every aggregate shows n. Descriptive only; no threshold recommendation. '
            'Overlapping reasons are not causal attribution. Preflight-only TA has selection bias. '
            'Sleeping service, budget and index coverage create missing/non-random outcomes. '
            'Provider pool-not-found is not proof of onchain death; returns are never imputed.</p>'
            + raw(learning["stats"]) + '<details><summary>Collection configuration</summary>'
            + raw(learning["config"]) + '</details>')
    if not any(s["observations"] for s in state["active"] + state["recent"]):
        out += '<p>First horizons pending; no completed follow-up observations yet.</p>'
    rows = []
    for s in sorted(state["active"] + state["recent"], key=lambda s: s["at"], reverse=True):
        row = {"token": s["symbol"] or s["mint_address"], "verdict": s["verdict"],
               "reasons": s["reasons"], "decision_id": s["decision_id"], "at": s["at"]}
        for h in HORIZONS:
            obs = s["observations"].get(h, {})
            row[h] = (str(obs.get("return_pct") if obs.get("return_pct") is not None else "unavailable") + '%; liquidity change ' + str(obs.get("liquidity_change_pct") if obs.get("liquidity_change_pct") is not None else "unavailable")
                      + '%; ' + obs.get("status", "pending"))
        rows.append(row)
    out += '<p>Latest 200 sessions shown; pending checkpoint capped separately. Raw event records remain in durable cycle JSON.</p>'
    out += table(rows[:200], [("token", "Token"), ("at", "First evaluation UTC"), ("decision_id", "Decision reference"),
        ("verdict", "Verdict"), ("reasons", "Rejection reasons")] + [(h, h + " return / liquidity / status") for h in HORIZONS], "No tracked candidates")
    out += '<details><summary>Median outcomes by rejection reason (active + latest 200 terminal sessions)</summary>'
    out += table(learning["calibration"], [("horizon", "Horizon"), ("reason", "Reason"), ("n", "n returns"),
        ("missing", "Missing returns"), ("median", "Median return %"), ("min", "Min %"), ("max", "Max %")], "No outcome-return samples yet") + '</details>'
    for r in reports[:30]:
        if r.get("learning"):
            out += '<details><summary>Stored cycle distributions / coverage ' + text(r.get("finished_at")) + '</summary>' + raw(r["learning"]["distributions"]) + '</details>'
    return out + '<details><summary>Replayable follow-up events this cycle</summary>' + raw(learning["events"]) + '</details></section>'
