"""E2E test fixtures for cluster-guardian.

Starts a real uvicorn server with the full FastAPI + LangGraph stack.
The LLM is replaced with a FakeListChatModel unless OPENAI_API_KEY is set.
K8s client is mocked (no kind cluster required).

Env vars are set at module scope so that src modules imported during test
collection create Settings/clients with the right values. The top-level
conftest's _reset_singletons fixture restores pristine env vars for non-e2e
tests to prevent pollution.
"""

import asyncio
import os
import socket
from contextlib import asynccontextmanager
from unittest.mock import MagicMock, patch

import httpx
import pytest
import pytest_asyncio
import uvicorn

# ---------------------------------------------------------------------------
# Set env vars at module scope so src modules pick them up during import.
# The top-level conftest._reset_singletons restores pristine env for non-e2e
# tests, so these don't leak.
# ---------------------------------------------------------------------------
os.environ.setdefault(
    "CLUSTER_GUARDIAN_LLM_API_KEY",
    os.environ.get("OPENAI_API_KEY", "test-key-e2e"),
)
os.environ.setdefault("CLUSTER_GUARDIAN_LLM_PROVIDER", "openai")
os.environ.setdefault("CLUSTER_GUARDIAN_LLM_MODEL", "gpt-4o")
os.environ.setdefault("CLUSTER_GUARDIAN_K8SGPT_ENABLED", "false")
os.environ.setdefault("CLUSTER_GUARDIAN_EVENT_WATCH_ENABLED", "false")
os.environ.setdefault("CLUSTER_GUARDIAN_DEV_CONTROLLER_ENABLED", "false")
os.environ.setdefault("CLUSTER_GUARDIAN_SERVICE_DISCOVERY_ENABLED", "false")
os.environ.setdefault("CLUSTER_GUARDIAN_FAST_LOOP_INTERVAL_SECONDS", "3600")
os.environ.setdefault("CLUSTER_GUARDIAN_PROMETHEUS_URL", "http://localhost:9090")
os.environ.setdefault("CLUSTER_GUARDIAN_LOKI_URL", "http://localhost:3100")
os.environ.setdefault("CLUSTER_GUARDIAN_LONGHORN_URL", "http://localhost:8080")
os.environ.setdefault("CLUSTER_GUARDIAN_GATUS_URL", "http://localhost:8081")
os.environ.setdefault("CLUSTER_GUARDIAN_CROWDSEC_LAPI_URL", "http://localhost:8082")
os.environ.setdefault("CLUSTER_GUARDIAN_DEV_CONTROLLER_URL", "http://localhost:8083")


def pytest_collection_modifyitems(items):
    """Force all e2e tests onto the session event loop.

    The uvicorn server runs as an asyncio task in the session-scoped event
    loop.  Tests must share that loop so they can reach the server.
    """
    session_marker = pytest.mark.asyncio(loop_scope="session")
    for item in items:
        if "/e2e/" in str(item.fspath):
            item.add_marker(session_marker)


_FAKE_LLM_RESPONSE = (
    "I've analyzed the cluster. The pod health looks normal. "
    "No CrashLoopBackOff pods detected. All services are healthy. "
    "No immediate action is required."
)


def _free_port() -> int:
    """Find a free TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# Fake LLM factory for non-real-LLM tests
# ---------------------------------------------------------------------------


def _make_fake_llm():
    """Create a fake LLM that returns canned responses and supports bind_tools."""
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    class _ToolableFakeLLM(FakeListChatModel):
        """FakeListChatModel that supports bind_tools (returns self)."""

        def bind_tools(self, tools, **kwargs):
            return self

    # Provide many copies so the model never runs out of responses
    return _ToolableFakeLLM(responses=[_FAKE_LLM_RESPONSE] * 100)


# ---------------------------------------------------------------------------
# Guardian server fixture (session-scoped)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="session")
async def guardian_server():
    """Start a real uvicorn server on a random port.

    Patches K8s client, Redis, Qdrant to no-op so the server starts
    without real cluster access.  The LLM is replaced with a
    FakeListChatModel unless OPENAI_API_KEY is set.

    All env var changes are saved and restored on teardown to avoid
    polluting unit tests when running the full suite together.
    """
    port = _free_port()

    # HOST and PORT are server-specific, set them here (not at module scope)
    os.environ["CLUSTER_GUARDIAN_HOST"] = "127.0.0.1"
    os.environ["CLUSTER_GUARDIAN_PORT"] = str(port)

    # Import modules first so patch targets exist
    import src.k8s_client
    import src.redis_client
    import src.config
    import src.agent  # noqa: F401 -- ensure module exists for patching

    # Patch K8s config loading and client
    patches = [
        patch("src.k8s_client.config"),
        patch("src.k8s_client.client"),
    ]

    # Patch create_llm to return a fake model (unless using real LLM)
    if not os.environ.get("OPENAI_API_KEY"):
        fake_llm = _make_fake_llm()
        patches.append(patch("src.agent.create_llm", return_value=fake_llm))

    for p in patches:
        mock = p.start()
        if hasattr(mock, "CoreV1Api"):
            mock.CoreV1Api.return_value = MagicMock()
            mock.AppsV1Api.return_value = MagicMock()
            mock.BatchV1Api.return_value = MagicMock()
            mock.AutoscalingV2Api.return_value = MagicMock()
            mock.PolicyV1Api.return_value = MagicMock()
            mock.CustomObjectsApi.return_value = MagicMock()

    # Reset singletons so they pick up our patches
    src.k8s_client._k8s_client = None
    src.redis_client._redis_client = None
    src.agent._guardian = None

    # Reload config to pick up env vars
    src.config.settings = src.config.Settings()

    # Import and create the app
    from src.api import create_app, app_state

    app = create_app()

    # Configure uvicorn
    config = uvicorn.Config(
        app=app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
    )
    server = uvicorn.Server(config)

    # Start server in background task
    server_task = asyncio.create_task(server.serve())

    # Wait for server to be ready
    base_url = f"http://127.0.0.1:{port}"
    async with httpx.AsyncClient() as client:
        for _ in range(100):
            try:
                resp = await client.get(f"{base_url}/live")
                if resp.status_code == 200:
                    break
            except (httpx.ConnectError, httpx.ReadError):
                pass
            await asyncio.sleep(0.1)
        else:
            server.should_exit = True
            await server_task
            raise RuntimeError("Server did not start in time")

    # Wait for deferred init to complete (guardian initialization)
    async with httpx.AsyncClient() as client:
        for _ in range(150):
            try:
                resp = await client.get(f"{base_url}/ready")
                if resp.status_code == 200:
                    break
            except Exception:
                pass
            await asyncio.sleep(0.2)

    yield {
        "base_url": base_url,
        "ws_url": f"ws://127.0.0.1:{port}/ws",
        "port": port,
        "app_state": app_state,
    }

    # Shutdown
    server.should_exit = True
    await server_task

    for p in patches:
        p.stop()

    # Clean up fixture-specific env vars (module-scope vars are handled by
    # _reset_singletons in the top-level conftest)
    os.environ.pop("CLUSTER_GUARDIAN_HOST", None)
    os.environ.pop("CLUSTER_GUARDIAN_PORT", None)


# ---------------------------------------------------------------------------
# HTTP client fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def http_client(guardian_server):
    """httpx.AsyncClient pointed at the guardian server."""
    async with httpx.AsyncClient(
        base_url=guardian_server["base_url"],
        timeout=30.0,
    ) as client:
        yield client


# ---------------------------------------------------------------------------
# WebSocket helper
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _ws_connect(url: str):
    """Connect to a WebSocket endpoint and yield the connection."""
    import websockets

    async with websockets.connect(url) as ws:
        yield ws


@pytest.fixture
def ws_connect(guardian_server):
    """Return an async context manager for WebSocket connections."""
    ws_url = guardian_server["ws_url"]

    @asynccontextmanager
    async def connect():
        import websockets

        async with websockets.connect(ws_url) as ws:
            yield ws

    return connect
