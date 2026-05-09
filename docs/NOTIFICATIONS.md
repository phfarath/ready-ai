# Notifications Guide

Get notified when documentation events occur.

## Webhook Notifications

The default notification system sends HTTP POST requests to a configurable webhook URL.

## Configuration

Set the webhook URL via environment variable:

```bash
export NOTIFY_WEBHOOK_URL="$YOUR_WEBHOOK_URL"
```

Or in your batch config — notifications are sent automatically on relevant events.

## Events

| Event | Trigger | Payload |
|-------|---------|---------|
| `drift_detected` | Visual diff below threshold | run_id, step, similarity_score |
| `auto_healed` | Auto-heal completed | run_id, steps_updated, new_screenshots |
| `broken` | Step execution failed | run_id, step, error_message |
| `test_passed` | All tests passed | run_id, step_count, execution_time |
| `doc_generated` | New docs created | run_id, version, file_count |

## Payload Format

```json
{
  "event": "drift_detected",
  "timestamp": "2026-05-09T14:30:00Z",
  "run_id": "550e8400-e29b-41d4-a716-446655440000",
  "app_version": "2.3.1",
  "details": {
    "step": 3,
    "similarity_score": 0.72,
    "threshold": 0.85,
    "url": "https://app.example.com/dashboard"
  }
}
```

## Supported Platforms

### Slack

Set your Slack incoming webhook URL as `NOTIFY_WEBHOOK_URL`. ready-ai POSTs JSON payloads.

### Discord

Set your Discord webhook URL as `NOTIFY_WEBHOOK_URL`. Discord renders the JSON payload.

### Custom Webhook

Build your own receiver:

```python
from fastapi import FastAPI

app = FastAPI()

@app.post("/ready-ai-webhook")
async def handle_notification(payload: dict):
    event = payload["event"]
    run_id = payload["run_id"]

    if event == "drift_detected":
        await send_alert(f"Docs drifted for run {run_id}")
    elif event == "doc_generated":
        await send_notification(f"New docs generated: {run_id}")

    return {"status": "ok"}
```

## Notifications in Test Runner

The `DocTestRunner` sends notifications when:

1. **Drift detected** — screenshot similarity below threshold
2. **Auto-healed** — docs were updated automatically
3. **Broken** — step could not be re-executed
4. **Passed** — all tests successful

Example test output:

```
⚠️  UI drift detected in step 3 (similarity: 0.72)
📤 Notification sent: drift_detected
🔄 Auto-healing step 3...
✅ Step 3 healed (new screenshot captured)
📤 Notification sent: auto_healed
```

## Disabling Notifications

To disable notifications, unset `NOTIFY_WEBHOOK_URL`. The system is fire-and-forget — it will not fail if the webhook is unavailable.

## Future: Telegram

Telegram notifications are planned. Configuration will use:

```bash
export TELEGRAM_BOT_TOKEN="your-bot-token"
export TELEGRAM_CHAT_ID="-1001234567890"
```
