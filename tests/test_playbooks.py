"""Tests for the remediation playbook system."""

from src.playbooks import (
    BUILTIN_PLAYBOOKS,
    MatchRule,
    Operator,
    Playbook,
    PlaybookExecutor,
    PlaybookStep,
)


# ---------------------------------------------------------------------------
# MatchRule
# ---------------------------------------------------------------------------


class TestMatchRule:
    def test_equals(self):
        rule = MatchRule(
            field="alertname", operator=Operator.EQUALS, value="KubeNodeNotReady"
        )
        assert rule.matches({"alertname": "KubeNodeNotReady"}) is True
        assert rule.matches({"alertname": "Other"}) is False

    def test_contains(self):
        rule = MatchRule(
            field="alertname", operator=Operator.CONTAINS, value="CrashLoop"
        )
        assert rule.matches({"alertname": "KubePodCrashLooping"}) is True
        assert rule.matches({"alertname": "KubeNodeNotReady"}) is False

    def test_regex(self):
        rule = MatchRule(
            field="alertname", operator=Operator.REGEX, value=r"^Kube(Pod|Container)"
        )
        assert rule.matches({"alertname": "KubePodCrashLooping"}) is True
        assert rule.matches({"alertname": "KubeContainerOOMKilled"}) is True
        assert rule.matches({"alertname": "KubeNodeNotReady"}) is False

    def test_missing_field(self):
        rule = MatchRule(field="nonexistent", operator=Operator.EQUALS, value="x")
        assert rule.matches({"alertname": "test"}) is False


# ---------------------------------------------------------------------------
# PlaybookStep
# ---------------------------------------------------------------------------


class TestPlaybookStep:
    def test_render_args(self):
        step = PlaybookStep(
            name="Get logs",
            tool="get_pod_k8s_logs",
            args_template={
                "namespace": "{{namespace}}",
                "pod_name": "{{pod}}",
            },
        )
        args = step.render_args({"namespace": "default", "pod": "web-abc"})
        assert args == {"namespace": "default", "pod_name": "web-abc"}

    def test_render_args_missing_context(self):
        step = PlaybookStep(
            name="test",
            tool="test",
            args_template={"key": "{{missing}}"},
        )
        args = step.render_args({})
        assert args == {"key": "{{missing}}"}


# ---------------------------------------------------------------------------
# Playbook
# ---------------------------------------------------------------------------


class TestPlaybook:
    def test_matches_all_rules(self):
        pb = Playbook(
            id="test",
            name="Test",
            description="Test",
            match_rules=[
                MatchRule(
                    field="alertname", operator=Operator.CONTAINS, value="CrashLoop"
                ),
                MatchRule(field="namespace", operator=Operator.EQUALS, value="default"),
            ],
        )
        assert (
            pb.matches({"alertname": "KubePodCrashLooping", "namespace": "default"})
            is True
        )
        assert (
            pb.matches({"alertname": "KubePodCrashLooping", "namespace": "kube-system"})
            is False
        )

    def test_matches_no_rules_returns_false(self):
        pb = Playbook(id="test", name="Test", description="Test")
        assert pb.matches({"alertname": "anything"}) is False

    def test_to_dict(self):
        pb = Playbook(
            id="test",
            name="Test PB",
            description="Desc",
            steps=[PlaybookStep(name="s1", tool="t1")],
        )
        d = pb.to_dict()
        assert d["id"] == "test"
        assert d["name"] == "Test PB"
        assert len(d["steps"]) == 1

    def test_render_prompt(self):
        pb = Playbook(
            id="test",
            name="Test PB",
            description="Desc",
            steps=[
                PlaybookStep(
                    name="Get logs",
                    tool="get_logs",
                    args_template={"ns": "{{namespace}}"},
                ),
            ],
        )
        prompt = pb.render_prompt({"namespace": "default"})
        assert "Test PB" in prompt
        assert "get_logs" in prompt
        assert "ns=default" in prompt


# ---------------------------------------------------------------------------
# PlaybookExecutor
# ---------------------------------------------------------------------------


class TestPlaybookExecutor:
    def test_match_crashloop(self):
        executor = PlaybookExecutor()
        pb = executor.match({"alertname": "KubePodCrashLooping"})
        assert pb is not None
        assert pb.id == "crashloop"

    def test_match_oomkilled(self):
        executor = PlaybookExecutor()
        pb = executor.match({"alertname": "KubeContainerOOMKilled"})
        assert pb is not None
        assert pb.id == "oomkilled"

    def test_match_node_not_ready(self):
        executor = PlaybookExecutor()
        pb = executor.match({"alertname": "KubeNodeNotReady"})
        assert pb is not None
        assert pb.id == "node-not-ready"

    def test_no_match(self):
        executor = PlaybookExecutor()
        pb = executor.match({"alertname": "CustomUnknownAlert"})
        assert pb is None

    def test_render_for_agent_with_match(self):
        executor = PlaybookExecutor()
        result = executor.render_for_agent(
            {
                "alertname": "KubePodCrashLooping",
                "labels": {
                    "alertname": "KubePodCrashLooping",
                    "namespace": "default",
                    "pod": "web-abc",
                },
            }
        )
        assert result is not None
        assert "CrashLoopBackOff Recovery" in result
        assert "get_pod_k8s_logs" in result

    def test_render_for_agent_no_match(self):
        executor = PlaybookExecutor()
        result = executor.render_for_agent({"alertname": "UnknownAlert"})
        assert result is None

    def test_max_executions_limit(self):
        executor = PlaybookExecutor()
        for _ in range(3):
            executor.render_for_agent(
                {
                    "alertname": "KubePodCrashLooping",
                    "labels": {"alertname": "KubePodCrashLooping"},
                }
            )
        result = executor.render_for_agent(
            {
                "alertname": "KubePodCrashLooping",
                "labels": {"alertname": "KubePodCrashLooping"},
            }
        )
        assert "max auto-executions" in result

    def test_get_playbook_by_id(self):
        executor = PlaybookExecutor()
        pb = executor.get_playbook("crashloop")
        assert pb is not None
        assert pb.id == "crashloop"

    def test_get_playbook_not_found(self):
        executor = PlaybookExecutor()
        assert executor.get_playbook("nonexistent") is None

    def test_list_playbooks(self):
        executor = PlaybookExecutor()
        playbooks = executor.list_playbooks()
        assert len(playbooks) == len(BUILTIN_PLAYBOOKS)
        assert all("id" in pb for pb in playbooks)


# ---------------------------------------------------------------------------
# Built-in playbooks sanity
# ---------------------------------------------------------------------------


class TestBuiltinPlaybooks:
    def test_all_have_ids(self):
        for pb in BUILTIN_PLAYBOOKS:
            assert pb.id, f"Playbook missing ID: {pb.name}"

    def test_all_have_match_rules(self):
        for pb in BUILTIN_PLAYBOOKS:
            assert pb.match_rules, f"Playbook {pb.id} has no match rules"

    def test_all_have_steps(self):
        for pb in BUILTIN_PLAYBOOKS:
            assert pb.steps, f"Playbook {pb.id} has no steps"

    def test_expected_count(self):
        assert len(BUILTIN_PLAYBOOKS) == 7
