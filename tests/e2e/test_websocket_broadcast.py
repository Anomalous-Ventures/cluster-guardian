"""E2E tests for WebSocket event broadcasting."""

import asyncio
import json

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


class TestWebSocketBroadcast:
    async def test_ws_connect_and_receive_pong(self, ws_connect):
        async with ws_connect() as ws:
            await ws.send(json.dumps({"type": "ping"}))
            resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=5.0))
            assert resp["type"] == "pong"

    async def test_ws_receives_investigation_lifecycle(self, ws_connect, http_client):
        async with ws_connect() as ws:
            # Drain any initial messages
            await asyncio.sleep(0.2)

            # Trigger investigation
            resp = await http_client.post(
                "/api/v1/investigate",
                json={"description": "WebSocket lifecycle test"},
                timeout=60.0,
            )
            assert resp.status_code == 200
            inv_id = resp.json()["investigation_id"]

            # Collect WS messages for a few seconds
            messages = []
            try:
                while True:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5.0))
                    messages.append(msg)
            except asyncio.TimeoutError:
                pass

            # Verify lifecycle events
            types = [m["type"] for m in messages]
            assert "investigation_started" in types
            assert "investigation_completed" in types

            # Verify investigation_id consistency
            for msg in messages:
                if msg["type"] in ("investigation_started", "investigation_completed"):
                    assert msg["investigation_id"] == inv_id

    async def test_ws_investigation_step_has_reasoning(self, ws_connect, http_client):
        async with ws_connect() as ws:
            await asyncio.sleep(0.2)

            await http_client.post(
                "/api/v1/investigate",
                json={"description": "Reasoning visibility test"},
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
            # At least one step should have reasoning or tool_calls
            if steps:
                has_content = any(
                    m.get("data", {}).get("reasoning")
                    or m.get("data", {}).get("tool_calls")
                    or m.get("data", {}).get("tool_results")
                    for m in steps
                )
                assert has_content, f"No step had reasoning/tool content: {steps}"

    async def test_ws_multiple_clients_receive_broadcasts(
        self, ws_connect, http_client
    ):
        async with ws_connect() as ws1, ws_connect() as ws2:
            await asyncio.sleep(0.2)

            await http_client.post(
                "/api/v1/investigate",
                json={"description": "Multi-client broadcast test"},
                timeout=60.0,
            )

            async def collect(ws, timeout=5.0):
                msgs = []
                try:
                    while True:
                        msg = json.loads(
                            await asyncio.wait_for(ws.recv(), timeout=timeout)
                        )
                        msgs.append(msg)
                except asyncio.TimeoutError:
                    pass
                return msgs

            msgs1, msgs2 = await asyncio.gather(collect(ws1), collect(ws2))

            types1 = {m["type"] for m in msgs1}
            types2 = {m["type"] for m in msgs2}

            # Both clients should receive investigation events
            assert "investigation_started" in types1
            assert "investigation_started" in types2
