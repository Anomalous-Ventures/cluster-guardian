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

    # LLM Backend
    llm_provider: str = "openai"
    llm_base_url: Optional[str] = None
    llm_model: str = "gpt-4o"
    llm_api_key: Optional[str] = None

    # K8sGPT
    k8sgpt_url: Optional[str] = None
    k8sgpt_enabled: bool = True

    # AlertManager
    alertmanager_url: Optional[str] = None
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
    github_owner: str = ""
    github_repo: str = ""
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
    health_check_domain: Optional[str] = None
    monitored_services: List[str] = []
    ssl_warning_days: int = 30
    health_check_interval: int = 300

    # Memory/State
    redis_url: Optional[str] = None

    # Qdrant Vector Memory
    qdrant_url: Optional[str] = None
    qdrant_collection: str = "guardian_issues"

    # Langfuse Observability
    langfuse_public_key: Optional[str] = None
    langfuse_secret_key: Optional[str] = None
    langfuse_host: Optional[str] = None

    # Embedding
    embedding_model: str = "text-embedding-3-small"
    embedding_base_url: Optional[str] = None
    embedding_api_key: Optional[str] = None

    # Prometheus
    prometheus_url: Optional[str] = None

    # Loki
    loki_url: Optional[str] = None

    # CrowdSec
    crowdsec_lapi_url: Optional[str] = None
    crowdsec_api_key: Optional[str] = None

    # Longhorn
    longhorn_url: Optional[str] = None

    # Gatus Status Page
    gatus_url: Optional[str] = None

    # Incident Correlation
    correlation_window_seconds: int = 300
    correlation_debounce_seconds: int = 30
    correlation_expiry_seconds: int = 3600

    # Continuous Monitor
    fast_loop_interval_seconds: int = 30
    event_watch_enabled: bool = True
    anomaly_suppression_window: int = 300
    anomaly_batch_window: int = 10

    # AI Dev Controller
    dev_controller_url: Optional[str] = None
    dev_controller_enabled: bool = True
    escalation_threshold: int = 3

    # Log Anomaly Detection
    log_anomaly_min_count: int = 10
    log_anomaly_window: str = "5m"

    # Dynamic Service Discovery
    service_discovery_enabled: bool = True
    service_discovery_interval_loops: int = 10

    # Auto-escalation for recurring issues
    auto_escalate_recurring: bool = True

    class Config:
        env_prefix = "CLUSTER_GUARDIAN_"


settings = Settings()
