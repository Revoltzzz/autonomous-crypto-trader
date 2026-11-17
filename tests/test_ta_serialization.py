"""Regression for deployed cycle b512145a: frozen scanner errors in TA."""
import asyncio
import json
from types import MappingProxyType

import httpx
import pytest

from act.agents import Context, Registry, freeze, run_agents
from act.agents.scanner import TechnicalAnalysisAgent


@pytest.mark.parametrize("market", ["spot", "futures"])
def test_ta_frozen_scanner_error_serializes_without_fetches(market):
    error = {"type": "GeoBlocked", "reason": "HTTP 451", "details": {"codes": [451]}}
    snapshot = freeze({"agents": {"scanner_" + market: {
        "status": "success", "data": {"status": "failed", "candidates": [], "error": error}}}})
    original = snapshot["agents"]["scanner_" + market]["data"]["error"]
    assert isinstance(original, MappingProxyType)
    async def run():
        async with httpx.AsyncClient() as client:
            context = Context(snapshot, client, 12, 6)
            result = await TechnicalAnalysisAgent(market).run(context)
            assert context.used == 0
            assert context.provenance == []
            assert json.loads(json.dumps(result, allow_nan=False))["scanner_error"] == error
            assert isinstance(original, MappingProxyType)
    asyncio.run(run())


def test_registry_thaws_nested_agent_result_before_json_boundary():
    class FrozenAgent:
        name = "frozen_fixture"
        async def run(self, context):
            return {"nested": MappingProxyType({"items": (MappingProxyType({"n": 1}),)})}
    registry = Registry()
    registry.register(FrozenAgent())
    result = asyncio.run(run_agents({"paper_only": True}, registry=registry))
    assert result["frozen_fixture"]["status"] == "success"
    assert json.loads(json.dumps(result))["frozen_fixture"]["data"] == {"nested": {"items": [{"n": 1}]}}
