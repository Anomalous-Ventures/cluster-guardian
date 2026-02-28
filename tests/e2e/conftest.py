"""E2E test fixtures for cluster-guardian.

Starts a real uvicorn server with the full FastAPI + LangGraph stack.
The LLM is replaced with a FakeListChatModel unless OPENAI_API_KEY is set.
K8s client is mocked (no kind cluster required).
"""

import asyncio
import os
import socket
import sys
from contextlib import asynccontextmanager
from unittest.mock import MagicMock, patch

import httpx
import pytest
import pytest_asyncio
import uvicorn

# ---------------------------------------------------------------------------
# Ensure CLUSTER_GUARDIAN_E2E is set so the top-level conftest.py skips stubs
# ---------------------------------------------------------------------------
os.environ["CLUSTER_GUARDIAN_E2E"] = "1"


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
# K8s mock helpers
# ---------------------------------------------------------------------------


def _mock_k8s_modules():
    """Stub kubernetes modules with MagicMock so imports succeed."""
    k8s_modules = [
        "kubernetes",
        "kubernetes.client",
        "kubernetes.client.rest",
        "kubernetes.config",
        "kubernetes.watch",
    ]
    for mod in k8s_modules:
        if mod not in sys.modules or isinstance(sys.modules[mod], MagicMock):
            sys.modules[mod] = MagicMock()

    # grpc / protobuf stubs
    grpc_modules = [
        "grpc",
        "grpc.aio",
        "grpc._channel",
        "google.protobuf.runtime_version",
    ]
    for mod in grpc_modules:
        if mod not in sys.modules or isinstance(sys.modules[mod], MagicMock):
            sys.modules[mod] = MagicMock()

    # qdrant stubs
    qdrant_modules = [
        "qdrant_client",
        "qdrant_client.http",
        "qdrant_client.http.models",
    ]
    for mod in qdrant_modules:
        if mod not in sys.modules or isinstance(sys.modules[mod], MagicMock):
            sys.modules[mod] = MagicMock()

    # langfuse stubs
    langfuse_modules = [
        "langfuse",
        "langfuse.callback",
        "langfuse.batch_evaluation",
        "langfuse.api",
        "langfuse.api.resources",
    ]
    for mod in langfuse_modules:
        if mod not in sys.modules or isinstance(sys.modules[mod], MagicMock):
            sys.modules[mod] = MagicMock()

    # Proto stubs
    proto_mock = MagicMock()
    sys.modules.setdefault("src.proto", proto_mock)
    sys.modules.setdefault("src.proto.k8sgpt_pb2", proto_mock)
    sys.modules.setdefault("src.proto.k8sgpt_pb2_grpc", proto_mock)

    # qdrant realistic stubs
    _qdrant_mock = sys.modules["qdrant_client"]
    _qdrant_http_models = sys.modules["qdrant_client.http.models"]
    _qdrant_http_models.Distance = MagicMock()
    _qdrant_http_models.Distance.COSINE = "Cosine"
    _qdrant_http_models.PointStruct = MagicMock()
    _qdrant_http_models.VectorParams = MagicMock()
    _qdrant_mock.AsyncQdrantClient = MagicMock()


# Apply K8s mocks before any src imports
_mock_k8s_modules()


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
    """
    port = _free_port()

    # Set env vars for the server
    os.environ["CLUSTER_GUARDIAN_LLM_API_KEY"] = os.environ.get(
        "OPENAI_API_KEY", "test-key-e2e"
    )
    os.environ["CLUSTER_GUARDIAN_LLM_PROVIDER"] = "openai"
    os.environ["CLUSTER_GUARDIAN_LLM_MODEL"] = "gpt-4o"
    os.environ["CLUSTER_GUARDIAN_HOST"] = "127.0.0.1"
    os.environ["CLUSTER_GUARDIAN_PORT"] = str(port)
    os.environ["CLUSTER_GUARDIAN_K8SGPT_ENABLED"] = "false"
    os.environ["CLUSTER_GUARDIAN_EVENT_WATCH_ENABLED"] = "false"
    os.environ["CLUSTER_GUARDIAN_DEV_CONTROLLER_ENABLED"] = "false"
    os.environ["CLUSTER_GUARDIAN_SERVICE_DISCOVERY_ENABLED"] = "false"
    os.environ["CLUSTER_GUARDIAN_FAST_LOOP_INTERVAL_SECONDS"] = "3600"

    # Provide dummy URLs for optional services so constructors don't crash on
    # .rstrip("/") with None values.
    os.environ.setdefault("CLUSTER_GUARDIAN_PROMETHEUS_URL", "http://localhost:9090")
    os.environ.setdefault("CLUSTER_GUARDIAN_LOKI_URL", "http://localhost:3100")
    os.environ.setdefault("CLUSTER_GUARDIAN_LONGHORN_URL", "http://localhost:8080")
    os.environ.setdefault("CLUSTER_GUARDIAN_GATUS_URL", "http://localhost:8081")
    os.environ.setdefault("CLUSTER_GUARDIAN_CROWDSEC_LAPI_URL", "http://localhost:8082")
    os.environ.setdefault(
        "CLUSTER_GUARDIAN_DEV_CONTROLLER_URL", "http://localhost:8083"
    )

    # Import modules first so patch targets exist
    import src.k8s_client
    import src.redis_client
    import src.config
    import src.agent  # noqa: F401 -- ensure module exists for patching

    # Patch K8s config loading and client
    patches = [
        patch("src.k8s_client.config"),
        patch("src.k8s_client.client"),
        patch("src.continuous_monitor.settings"),
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
