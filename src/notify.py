"""
Notification System — sends alerts about documentation events.

Supported channels:
  - Webhook (generic HTTP POST to any URL)

Telegram is supported only when this code runs inside the Hermes Agent
environment; otherwise configure a webhook that posts to Hermes/Telegram.
"""

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class NotificationPayload:
    """Standard payload for all notification channels."""
    event_type: str          # "generated", "drift_detected", "auto_healed", "broken", "test_passed"
    title: str
    message: str
    details: dict
    timestamp: str = ""
    run_id: str = ""
    app_version: str = ""
    url: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()

    def _format_telegram(self) -> str:
        """Format for Telegram message. Use standalone if this is wired up."""
        icon_map = {
            "generated": "📝",
            "drift_detected": "⚠️",
            "auto_healed": "🔧",
            "broken": "❌",
            "test_passed": "✅",
        }
        icon = icon_map.get(self.event_type, "📋")
        lines = [
            f"{icon} *{self.title}*",
            "",
            self.message,
        ]
        if self.app_version:
            lines.append(f"Version: `{self.app_version}`")
        if self.run_id:
            lines.append(f"Run ID: `{self.run_id}`")
        if self.url:
            lines.append(f"URL: {self.url}")
        if self.details:
            lines.append("")
            for k, v in self.details.items():
                if isinstance(v, (int, float)):
                    lines.append(f"{k}: `{v}`")
                elif isinstance(v, str) and len(v) <= 100:
                    lines.append(f"{k}: `{v}`")
                else:
                    lines.append(f"{k}: _... (truncated)_")
        return "\n".join(lines)

    def _format_webhook(self) -> dict:
        """Format for generic webhook JSON."""
        return {
            "event_type": self.event_type,
            "title": self.title,
            "message": self.message,
            "details": self.details,
            "timestamp": self.timestamp,
            "run_id": self.run_id,
            "app_version": self.app_version,
            "url": self.url,
        }


class Notifier:
    """Send notifications to external channels."""

    def __init__(
        self,
        webhook_url: Optional[str] = None,
    ):
        self.webhook_url = webhook_url or os.environ.get("NOTIFY_WEBHOOK_URL", "")
        if not self.webhook_url:
            logger.warning(
                "No notification webhook configured. Set NOTIFY_WEBHOOK_URL env var."
            )

    async def send(self, payload: NotificationPayload) -> dict:
        """Send notification via configured channels."""
        return await self._send_webhook(payload)

    async def _send_webhook(self, payload: NotificationPayload) -> dict:
        """Send via generic webhook using aiohttp."""
        import aiohttp

        if not self.webhook_url:
            return {"channel": "webhook", "ok": False, "error": "no webhook_url configured"}

        try:
            data = payload._format_webhook()
            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    self.webhook_url,
                    json=data,
                    headers={"Content-Type": "application/json"},
                ) as resp:
                    return {
                        "channel": "webhook",
                        "ok": 200 <= resp.status < 300,
                        "status": resp.status,
                    }
        except Exception as e:
            logger.error(f"Webhook notification failed: {e}")
            return {"channel": "webhook", "ok": False, "error": str(e)}


def create_notifier() -> Notifier:
    return Notifier()


def load_from_env() -> Notifier:
    """Create a Notifier using environment configuration."""
    return Notifier()
