"""
Configuration for Cluster Guardian.
"""

from pydantic_settings import BaseSettings
from typing import List, Optional


class Settings(BaseSettings):
    """Application settings from environment variables."""

    # Server
    host: str = "0.0.0.0"
    port: int = 8900
    debug: bool = False

    # LLM Backend (via LiteLLM)
    llm_base_url: str = "http://litellm.llm.svc.cluster.local:4000/v1"
    llm_model: str = "llama3.2"
    llm_api_key: str = "sk-spark-litellm-master-key"

    # K8sGPT
    k8sgpt_url: str = "http://k8sgpt.k8sgpt.svc.cluster.local:8080"
    k8sgpt_enabled: bool = True

    # AlertManager
    alertmanager_url: str = "http://prometheus-kube-prometheus-alertmanager.prometheus.svc.cluster.local:9093"
    alertmanager_webhook_enabled: bool = True

    # Kubernetes
    # Uses in-cluster config by default
    kubeconfig_path: Optional[str] = None

    # Safety Controls
    # Namespaces that should NEVER be auto-remediated
    protected_namespaces: List[str] = [
        "kube-system",
        "kube-public",
        "kube-node-lease",
        "longhorn-system",
        "calico-system",
        "tigera-operator",
    ]

    # Maximum actions per hour (rate limiting)
    max_actions_per_hour: int = 30

    # Require human approval for these action types
    require_approval_for: List[str] = [
        "delete_pvc",
        "cordon_node",
        "drain_node",
        "scale_to_zero",
    ]

    # Scan interval in seconds
    scan_interval_seconds: int = 300  # 5 minutes

    # GitHub PR Integration
    github_token: Optional[str] = None
    github_owner: str = "Anomalous-Ventures"
    github_repo: str = "stax"
    github_base_branch: str = "main"

    # TheHive
    thehive_url: Optional[str] = None
    thehive_api_key: Optional[str] = None

    # Wazuh Syslog
    wazuh_syslog_host: Optional[str] = None
    wazuh_syslog_port: int = 1514

    # Notification - Slack
    slack_webhook_url: Optional[str] = None
    notification_channel: str = "#alerts"

    # Notification - Email (SMTP)
    email_smtp_host: Optional[str] = None
    email_smtp_port: int = 587
    email_smtp_tls: bool = True
    email_smtp_user: Optional[str] = None
    email_smtp_password: Optional[str] = None
    email_from: Optional[str] = None
    email_recipients: List[str] = []

    # Notification - Discord
    discord_webhook_url: Optional[str] = None

    # Notification - Microsoft Teams
    teams_webhook_url: Optional[str] = None

    # Notification - PagerDuty
    pagerduty_integration_key: Optional[str] = None

    # Notification - Custom Webhook
    custom_webhook_url: Optional[str] = None
    custom_webhook_method: str = "POST"
    custom_webhook_headers: str = "{}"

    # Notification - General
    notification_rate_limit: int = 60

    # Autonomy
    autonomy_level: str = "conditional"

    # Agent
    max_agent_iterations: int = 25
    log_level: str = "info"

    # Safety - Quiet Hours
    quiet_hours_start: Optional[str] = None
    quiet_hours_end: Optional[str] = None
    quiet_hours_tz: str = "UTC"
    dry_run_mode: bool = False

    # Health Check Tuning
    monitored_services: List[str] = []
    ssl_warning_days: int = 30
    health_check_interval: int = 300

    # Memory/State
    redis_url: str = "redis://redis-ai-master.llm.svc.cluster.local:6379"

    # Qdrant Vector Memory
    qdrant_url: str = "http://qdrant.llm.svc.cluster.local:6333"
    qdrant_collection: str = "guardian_issues"

    # Langfuse Observability
    langfuse_public_key: Optional[str] = None
    langfuse_secret_key: Optional[str] = None
    langfuse_host: Optional[str] = None

    # Embedding
    embedding_model: str = "text-embedding-3-small"

    # Prometheus
    prometheus_url: str = "http://prometheus-kube-prometheus-prometheus.prometheus.svc.cluster.local:9090"

    # Loki
    loki_url: str = "http://loki.prometheus.svc.cluster.local:3100"

    # CrowdSec
    crowdsec_lapi_url: str = "http://crowdsec-lapi.crowdsec.svc.cluster.local:8080"
    crowdsec_api_key: Optional[str] = None

    # Longhorn
    longhorn_url: str = "http://longhorn-frontend.longhorn-system.svc.cluster.local:8000"

    # Gatus Status Page
    gatus_url: str = "http://gatus.status.svc.cluster.local:80"

    class Config:
        env_prefix = "CLUSTER_GUARDIAN_"


settings = Settings()
