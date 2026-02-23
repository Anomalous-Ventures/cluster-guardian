"""Safety-critical tests for the K8s client."""

import sys
from unittest.mock import MagicMock, patch

import pytest

from src.config import settings


def _ensure_agent_importable():
    """Pre-mock heavy dependencies so ``import src.agent`` succeeds.

    The conftest ``_reset_singletons`` autouse fixture imports ``src.agent``,
    which pulls in langfuse (broken on Python 3.14 + pydantic v1) and protobuf
    (gencode/runtime mismatch).  Stub them out before pytest collection if they
    cannot be imported natively.
    """
    stubs = [
        "langfuse",
        "langfuse.callback",
        "langfuse.batch_evaluation",
        "langfuse.api",
        "langfuse.api.resources",
        "langfuse.decorators",
        "grpc",
    ]
    for name in stubs:
        if name not in sys.modules:
            try:
                __import__(name)
            except Exception:
                sys.modules[name] = MagicMock()

    # Stub the protobuf-generated k8sgpt modules if they fail to import.
    for name in ["src.proto.k8sgpt_pb2", "src.proto.k8sgpt_pb2_grpc"]:
        if name not in sys.modules:
            try:
                __import__(name)
            except Exception:
                sys.modules[name] = MagicMock()


_ensure_agent_importable()


# All write operations and their call signatures (namespace-scoped).
NAMESPACED_WRITE_OPS = [
    ("restart_pod", {"namespace": "NS", "name": "pod-1", "reason": "test"}),
    (
        "scale_deployment",
        {"namespace": "NS", "name": "deploy-1", "replicas": 3, "reason": "test"},
    ),
    ("rollout_restart", {"namespace": "NS", "name": "deploy-1", "reason": "test"}),
    ("rollback_deployment", {"namespace": "NS", "name": "deploy-1", "reason": "test"}),
    (
        "rollout_restart_statefulset",
        {"namespace": "NS", "name": "sts-1", "reason": "test"},
    ),
    ("delete_failed_job", {"namespace": "NS", "name": "job-1", "reason": "test"}),
    ("delete_pvc", {"namespace": "NS", "name": "pvc-1", "reason": "test"}),
]


@pytest.mark.asyncio
class TestNamespaceProtection:
    """Namespace protection must block every write operation."""

    @pytest.mark.parametrize("protected_ns", settings.protected_namespaces)
    @pytest.mark.parametrize("op_name,kwargs_template", NAMESPACED_WRITE_OPS)
    async def test_namespace_protection_all_write_ops(
        self, mock_k8s_client, protected_ns, op_name, kwargs_template
    ):
        kwargs = {
            k: (protected_ns if v == "NS" else v) for k, v in kwargs_template.items()
        }
        method = getattr(mock_k8s_client, op_name)
        result = await method(**kwargs)

        assert result["success"] is False
        assert "protected" in result["error"].lower()
        assert protected_ns in result["error"]


@pytest.mark.asyncio
class TestRestartPod:
    """Tests for the restart_pod operation."""

    async def test_restart_pod_success(self, mock_k8s_client):
        result = await mock_k8s_client.restart_pod(
            namespace="default", name="my-pod", reason="CrashLoopBackOff"
        )

        assert result["success"] is True
        assert "my-pod" in result["message"]
        mock_k8s_client.core_v1.delete_namespaced_pod.assert_called_once_with(
            "my-pod", "default"
        )

    @pytest.mark.parametrize("ns", settings.protected_namespaces)
    async def test_restart_pod_protected_namespace(self, mock_k8s_client, ns):
        result = await mock_k8s_client.restart_pod(
            namespace=ns, name="coredns-abc", reason="test"
        )

        assert result["success"] is False
        assert "protected" in result["error"].lower()
        mock_k8s_client.core_v1.delete_namespaced_pod.assert_not_called()


@pytest.mark.asyncio
class TestScaleDeployment:
    """Tests for the scale_deployment operation."""

    async def test_scale_to_zero_requires_approval(self, mock_k8s_client):
        assert "scale_to_zero" in settings.require_approval_for

        result = await mock_k8s_client.scale_deployment(
            namespace="default", name="web", replicas=0, reason="scale down"
        )

        assert result["success"] is False
        assert result["requires_approval"] is True
        mock_k8s_client.apps_v1.patch_namespaced_deployment_scale.assert_not_called()

    async def test_scale_deployment_success(self, mock_k8s_client):
        result = await mock_k8s_client.scale_deployment(
            namespace="default", name="web", replicas=5, reason="load increase"
        )

        assert result["success"] is True
        assert "web" in result["message"]
        assert "5" in result["message"]
        mock_k8s_client.apps_v1.patch_namespaced_deployment_scale.assert_called_once_with(
            "web", "default", {"spec": {"replicas": 5}}
        )


@pytest.mark.asyncio
class TestApprovalGates:
    """Tests for operations that require human approval."""

    async def test_cordon_node_requires_approval(self, mock_k8s_client):
        assert "cordon_node" in settings.require_approval_for

        result = await mock_k8s_client.cordon_node(name="node-1", reason="unhealthy")

        assert result["success"] is False
        assert result["requires_approval"] is True
        mock_k8s_client.core_v1.patch_node.assert_not_called()

    async def test_drain_node_requires_approval(self, mock_k8s_client):
        assert "drain_node" in settings.require_approval_for

        result = await mock_k8s_client.drain_node(name="node-1", reason="maintenance")

        assert result["success"] is False
        assert result["requires_approval"] is True
        mock_k8s_client.core_v1.patch_node.assert_not_called()

    async def test_delete_pvc_requires_approval(self, mock_k8s_client):
        assert "delete_pvc" in settings.require_approval_for

        result = await mock_k8s_client.delete_pvc(
            namespace="default", name="data-pvc-0", reason="stuck"
        )

        assert result["success"] is False
        assert result["requires_approval"] is True
        mock_k8s_client.core_v1.delete_namespaced_persistent_volume_claim.assert_not_called()


@pytest.mark.asyncio
class TestRateLimiting:
    """Rate limiter must block actions when exhausted."""

    async def test_rate_limit_blocks_action(self, mock_k8s_client):
        # Exhaust the rate limiter: fill actions via record_action so both
        # in-memory deque AND Redis sorted set have entries.
        mock_k8s_client.rate_limiter.max_actions = 2

        # Patch _refresh_max_actions so it doesn't override max_actions.
        with patch.object(
            mock_k8s_client.rate_limiter, "_refresh_max_actions", return_value=None
        ):
            await mock_k8s_client.rate_limiter.record_action("action1")
            await mock_k8s_client.rate_limiter.record_action("action2")

            result = await mock_k8s_client.restart_pod(
                namespace="default", name="pod-x", reason="test"
            )

        assert result["success"] is False
        assert "rate limit" in result["error"].lower()
        mock_k8s_client.core_v1.delete_namespaced_pod.assert_not_called()


@pytest.mark.asyncio
class TestDrainNode:
    """Tests for the drain_node operation."""

    async def test_drain_node_skips_protected_and_daemonset(self, mock_k8s_client):
        # Remove drain_node from require_approval_for so we can reach the logic.
        original = list(settings.require_approval_for)
        settings.require_approval_for = [
            a for a in settings.require_approval_for if a != "drain_node"
        ]

        try:
            # Build mock pods on the node.
            def _make_pod(ns, name, owner_kind=None):
                pod = MagicMock()
                pod.metadata.namespace = ns
                pod.metadata.name = name
                if owner_kind:
                    ref = MagicMock()
                    ref.kind = owner_kind
                    pod.metadata.owner_references = [ref]
                else:
                    pod.metadata.owner_references = []
                return pod

            protected_pod = _make_pod("kube-system", "coredns-abc", "Deployment")
            daemonset_pod = _make_pod("default", "node-exporter-xyz", "DaemonSet")
            normal_pod = _make_pod("default", "web-abc", "ReplicaSet")

            pod_list = MagicMock()
            pod_list.items = [protected_pod, daemonset_pod, normal_pod]
            mock_k8s_client.core_v1.list_pod_for_all_namespaces.return_value = pod_list

            result = await mock_k8s_client.drain_node(
                name="worker-1", reason="disk pressure"
            )

            assert result["success"] is True

            # Only the normal pod should have been evicted.
            assert len(result["evicted"]) == 1
            assert "default/web-abc" in result["evicted"]

            # Protected-namespace pod and DaemonSet pod should be skipped.
            assert len(result["skipped"]) == 2
            skipped_str = " ".join(result["skipped"])
            assert "kube-system/coredns-abc" in skipped_str
            assert "node-exporter-xyz" in skipped_str

            # Node should have been cordoned.
            mock_k8s_client.core_v1.patch_node.assert_called_once_with(
                "worker-1", {"spec": {"unschedulable": True}}
            )

            # Eviction should have been called exactly once (for the normal pod).
            mock_k8s_client.core_v1.create_namespaced_pod_eviction.assert_called_once()
        finally:
            settings.require_approval_for = original


@pytest.mark.asyncio
class TestAuditLog:
    """Audit log must record entries for successful actions."""

    async def test_audit_log_records_actions(self, mock_k8s_client):
        assert len(mock_k8s_client.audit_log.entries) == 0

        await mock_k8s_client.restart_pod(
            namespace="default", name="app-pod", reason="OOMKilled"
        )

        entries = mock_k8s_client.audit_log.entries
        assert len(entries) == 1
        entry = entries[0]
        assert entry["action"] == "restart_pod"
        assert entry["target"] == "app-pod"
        assert entry["namespace"] == "default"
        assert entry["result"] == "success"
        assert entry["reason"] == "OOMKilled"
        assert "timestamp" in entry
