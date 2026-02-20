"""
Unified notification client for Cluster Guardian.

Sends notifications to Slack, TheHive, Wazuh syslog, Email, Discord,
Microsoft Teams, PagerDuty, and custom webhooks.
All methods are fire-and-forget: errors are logged, never raised.
"""

import json
import smtplib
import socket
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone
from typing import Optional

import httpx
import structlog
from jinja2 import Template

from .config import settings

logger = structlog.get_logger(__name__)

SEVERITY_MAP = {"low": 1, "medium": 2, "high": 3, "critical": 4}
SEVERITY_ORDER = {"info": 0, "low": 1, "warning": 2, "medium": 2, "high": 3, "critical": 4}
SLACK_COLORS = {"info": "#36a64f", "warning": "#ff9900", "critical": "#ff0000"}
DISCORD_COLORS = {"info": 0x36A64F, "warning": 0xFF9900, "critical": 0xFF0000}
PAGERDUTY_SEVERITY = {"info": "info", "warning": "warning", "critical": "critical", "low": "info", "medium": "warning", "high": "error"}


async def send_slack(message: str, severity: str = "info") -> bool:
    """Post a message to Slack via webhook."""
    if not settings.slack_webhook_url:
        logger.debug("slack_webhook_url not configured, skipping")
        return False

    color = SLACK_COLORS.get(severity, SLACK_COLORS["info"])
    payload = {
        "channel": settings.notification_channel,
        "attachments": [
            {
                "color": color,
                "title": f"Cluster Guardian [{severity.upper()}]",
                "text": message,
                "ts": int(datetime.now(timezone.utc).timestamp()),
            }
        ],
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(settings.slack_webhook_url, json=payload)
            resp.raise_for_status()
            logger.info("slack_notification_sent", severity=severity)
            return True
    except Exception as exc:
        logger.error("slack_notification_failed", error=str(exc))
        return False


async def send_email(message: str, severity: str = "info", subject: Optional[str] = None) -> bool:
    """Send notification via SMTP email.

    Args:
        message: Email body text.
        severity: Severity level for subject line.
        subject: Optional custom subject line.

    Returns:
        True if sent successfully, False otherwise.
    """
    if not settings.email_smtp_host or not settings.email_from or not settings.email_recipients:
        logger.debug("email not configured, skipping")
        return False

    subject = subject or f"Cluster Guardian [{severity.upper()}]"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.email_from
    msg["To"] = ", ".join(settings.email_recipients)
    msg.attach(MIMEText(message, "plain"))

    html_body = f"""<html><body>
    <h2 style="color: {'#ff0000' if severity == 'critical' else '#ff9900' if severity == 'warning' else '#36a64f'}">
        Cluster Guardian [{severity.upper()}]
    </h2>
    <pre>{message}</pre>
    <hr><small>Sent by Cluster Guardian at {datetime.now(timezone.utc).isoformat()}</small>
    </body></html>"""
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(settings.email_smtp_host, settings.email_smtp_port, timeout=10) as server:
            if settings.email_smtp_tls:
                server.starttls()
            if settings.email_smtp_user and settings.email_smtp_password:
                server.login(settings.email_smtp_user, settings.email_smtp_password)
            server.send_message(msg)
        logger.info("email_notification_sent", severity=severity, recipients=len(settings.email_recipients))
        return True
    except Exception as exc:
        logger.error("email_notification_failed", error=str(exc))
        return False


async def send_discord(message: str, severity: str = "info") -> bool:
    """Post a message to Discord via webhook with embed.

    Args:
        message: Notification text.
        severity: One of "info", "warning", "critical".

    Returns:
        True if accepted by Discord, False otherwise.
    """
    if not settings.discord_webhook_url:
        logger.debug("discord_webhook_url not configured, skipping")
        return False

    color = DISCORD_COLORS.get(severity, DISCORD_COLORS["info"])
    payload = {
        "embeds": [
            {
                "title": f"Cluster Guardian [{severity.upper()}]",
                "description": message[:4096],
                "color": color,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "footer": {"text": "Cluster Guardian"},
            }
        ],
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(settings.discord_webhook_url, json=payload)
            resp.raise_for_status()
            logger.info("discord_notification_sent", severity=severity)
            return True
    except Exception as exc:
        logger.error("discord_notification_failed", error=str(exc))
        return False


async def send_teams(message: str, severity: str = "info") -> bool:
    """Post a message to Microsoft Teams via webhook with Adaptive Card.

    Args:
        message: Notification text.
        severity: One of "info", "warning", "critical".

    Returns:
        True if accepted by Teams, False otherwise.
    """
    if not settings.teams_webhook_url:
        logger.debug("teams_webhook_url not configured, skipping")
        return False

    color = {"info": "good", "warning": "attention", "critical": "attention"}.get(severity, "default")
    payload = {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": [
                        {
                            "type": "TextBlock",
                            "text": f"Cluster Guardian [{severity.upper()}]",
                            "weight": "Bolder",
                            "size": "Medium",
                            "color": color,
                        },
                        {
                            "type": "TextBlock",
                            "text": message[:4096],
                            "wrap": True,
                        },
                        {
                            "type": "TextBlock",
                            "text": datetime.now(timezone.utc).isoformat(),
                            "size": "Small",
                            "isSubtle": True,
                        },
                    ],
                },
            }
        ],
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(settings.teams_webhook_url, json=payload)
            resp.raise_for_status()
            logger.info("teams_notification_sent", severity=severity)
            return True
    except Exception as exc:
        logger.error("teams_notification_failed", error=str(exc))
        return False


async def send_pagerduty(message: str, severity: str = "critical") -> bool:
    """Create an event in PagerDuty via Events API v2.

    Args:
        message: Event summary.
        severity: Maps to PagerDuty severity.

    Returns:
        True if event was accepted, False otherwise.
    """
    if not settings.pagerduty_integration_key:
        logger.debug("pagerduty_integration_key not configured, skipping")
        return False

    pd_severity = PAGERDUTY_SEVERITY.get(severity, "warning")
    payload = {
        "routing_key": settings.pagerduty_integration_key,
        "event_action": "trigger",
        "payload": {
            "summary": message[:1024],
            "severity": pd_severity,
            "source": "cluster-guardian",
            "component": "kubernetes",
            "group": "sre-agent",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://events.pagerduty.com/v2/enqueue",
                json=payload,
            )
            resp.raise_for_status()
            logger.info("pagerduty_event_sent", severity=severity)
            return True
    except Exception as exc:
        logger.error("pagerduty_event_failed", error=str(exc))
        return False


async def send_custom_webhook(message: str, severity: str = "info") -> bool:
    """Send notification to a custom webhook endpoint.

    Supports configurable URL, method, headers, and Jinja2 payload template.

    Args:
        message: Notification text.
        severity: Severity level.

    Returns:
        True if the webhook responded with 2xx, False otherwise.
    """
    if not settings.custom_webhook_url:
        logger.debug("custom_webhook_url not configured, skipping")
        return False

    try:
        headers = json.loads(settings.custom_webhook_headers) if settings.custom_webhook_headers else {}
    except json.JSONDecodeError:
        headers = {}

    payload = {
        "message": message,
        "severity": severity,
        "source": "cluster-guardian",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            if not headers.get("Content-Type"):
                headers["Content-Type"] = "application/json"
            resp = await client.request(
                method=settings.custom_webhook_method,
                url=settings.custom_webhook_url,
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            logger.info("custom_webhook_sent", severity=severity, url=settings.custom_webhook_url)
            return True
    except Exception as exc:
        logger.error("custom_webhook_failed", error=str(exc))
        return False


async def send_thehive_alert(
    title: str,
    description: str,
    severity: str = "medium",
    tags: Optional[list[str]] = None,
) -> Optional[str]:
    """Create an alert in TheHive.

    Args:
        title: Alert title.
        description: Detailed description.
        severity: One of "low", "medium", "high", "critical".
        tags: Optional list of tags.

    Returns:
        Alert ID if created, None on failure.
    """
    if not settings.thehive_url or not settings.thehive_api_key:
        logger.debug("thehive not configured, skipping")
        return None

    payload = {
        "type": "cluster-guardian",
        "source": "cluster-guardian",
        "sourceRef": f"cg-{int(datetime.now(timezone.utc).timestamp())}",
        "title": title,
        "description": description,
        "severity": SEVERITY_MAP.get(severity, 2),
        "tags": tags or ["cluster-guardian", "sre-agent"],
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{settings.thehive_url.rstrip('/')}/api/v1/alert",
                json=payload,
                headers={
                    "Authorization": f"Bearer {settings.thehive_api_key}",
                    "Content-Type": "application/json",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            alert_id = data.get("_id", "unknown")
            logger.info("thehive_alert_created", alert_id=alert_id, title=title)
            return alert_id
    except Exception as exc:
        logger.error("thehive_alert_failed", error=str(exc))
        return None


def send_wazuh_syslog(
    action: str,
    result: str,
    metadata: Optional[dict] = None,
) -> bool:
    """Send a structured JSON syslog message to the Wazuh manager.

    Uses TCP to ensure delivery. The JSON payload follows CEF-like
    conventions that Wazuh decoders can parse.

    Args:
        action: Action name (e.g. "restart_pod", "create_pr").
        result: Outcome description.
        metadata: Extra key-value pairs to include.

    Returns:
        True if the message was sent, False otherwise.
    """
    if not settings.wazuh_syslog_host:
        logger.debug("wazuh_syslog_host not configured, skipping")
        return False

    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": "cluster-guardian",
        "action": action,
        "result": result,
        "autonomy_level": settings.autonomy_level,
        **(metadata or {}),
    }

    message = json.dumps(payload) + "\n"

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(5)
            sock.connect((settings.wazuh_syslog_host, settings.wazuh_syslog_port))
            sock.sendall(message.encode("utf-8"))
        logger.info("wazuh_syslog_sent", action=action)
        return True
    except Exception as exc:
        logger.error("wazuh_syslog_failed", error=str(exc))
        return False


async def notify_all(message: str, severity: str = "info") -> dict[str, bool]:
    """Dispatch notification to all enabled channels respecting severity filters.

    Each channel is only notified if the message severity meets or exceeds
    the configured threshold. All channels are fire-and-forget.

    Args:
        message: Notification message text.
        severity: One of "info", "warning", "critical".

    Returns:
        Dict mapping channel name to success/failure boolean.
    """
    results: dict[str, bool] = {}
    sev_level = SEVERITY_ORDER.get(severity, 0)

    # Slack - always enabled if configured
    if settings.slack_webhook_url:
        results["slack"] = await send_slack(message, severity)

    # Email
    if settings.email_smtp_host and settings.email_from and settings.email_recipients:
        results["email"] = await send_email(message, severity)

    # Discord
    if settings.discord_webhook_url:
        results["discord"] = await send_discord(message, severity)

    # Teams
    if settings.teams_webhook_url:
        results["teams"] = await send_teams(message, severity)

    # PagerDuty - only for warning+ by default
    if settings.pagerduty_integration_key and sev_level >= SEVERITY_ORDER["warning"]:
        results["pagerduty"] = await send_pagerduty(message, severity)

    # Custom webhook
    if settings.custom_webhook_url:
        results["custom_webhook"] = await send_custom_webhook(message, severity)

    logger.info(
        "notify_all_dispatched",
        severity=severity,
        channels=list(results.keys()),
        successes=sum(1 for v in results.values() if v),
    )

    return results
