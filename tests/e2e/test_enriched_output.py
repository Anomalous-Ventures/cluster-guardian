"""E2E tests for enriched output format with real LLM data flow."""

import asyncio
import json
from datetime import datetime

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


class TestEnrichedOutput:
    async def test_investigation_started_has_required_fields(
        self, ws_connect, http_client
    ):
        async with ws_connect() as ws:
            await asyncio.sleep(0.2)

            await http_client.post(
                "/api/v1/investigate",
                json={"description": "Enriched output field test"},
                timeout=60.0,
            )

            messages = []
            try:
                while True:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5.0))
                    messages.append(msg)
            except asyncio.TimeoutError:
                pass

            started = [m for m in messages if m["type"] == "investigation_started"]
            assert len(started) >= 1, f"No investigation_started event: {messages}"
            ev = started[0]

            assert ev["type"] == "investigation_started"
            assert ev["investigation_id"].startswith("inv-")
            # Timestamp should be ISO 8601
            assert "T" in ev["timestamp"]
            datetime.fromisoformat(ev["timestamp"])  # validate parseable
            assert ev["data"]["description"] == "Enriched output field test"

    async def test_investigation_completed_has_summary(self, ws_connect, http_client):
        async with ws_connect() as ws:
            await asyncio.sleep(0.2)

            await http_client.post(
                "/api/v1/investigate",
                json={"description": "Completion enrichment test"},
                timeout=60.0,
            )

            messages = []
            try:
                while True:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5.0))
                    messages.append(msg)
            except asyncio.TimeoutError:
                pass

            completed = [m for m in messages if m["type"] == "investigation_completed"]
            assert len(completed) >= 1, f"No investigation_completed: {messages}"
            ev = completed[0]

            assert "summary" in ev["data"]
            assert ev["data"]["summary"]  # non-empty
            assert isinstance(ev["data"]["actions_taken"], list)
            assert isinstance(ev["data"]["duration_seconds"], (int, float))
            assert ev["data"]["duration_seconds"] >= 0
            assert ev["data"]["status"] in ("completed", "failed")

    async def test_investigation_step_has_node_info(self, ws_connect, http_client):
        async with ws_connect() as ws:
            await asyncio.sleep(0.2)

            await http_client.post(
                "/api/v1/investigate",
                json={"description": "Step node info test"},
                timeout=60.0,
            )

            messages = []
            try:
                while True:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5.0))
                    messages.append(msg)
            except asyncio.TimeoutError:
                pass

            steps = [m for m in messages if m["type"] == "investigation_step"]
            if steps:
                for step in steps:
                    if step.get("data"):
                        # Should have a node identifier
                        assert step["data"].get("node") in (
                            "agent",
                            "tools",
                            None,
                        )

    async def test_response_includes_investigation_id(self, http_client):
        resp = await http_client.post(
            "/api/v1/investigate",
            json={"description": "ID in response test"},
            timeout=60.0,
        )
        data = resp.json()
        assert "investigation_id" in data
        assert data["investigation_id"].startswith("inv-")
