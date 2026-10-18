# NoX Integrations

This directory is an extension point for future integrations that add tools
and data sources to NoX.

## Integration Protocol

Each integration implements the `Integration` protocol:

```python
from typing import Protocol
from noxai.tools.registry import ToolSpec


class Integration(Protocol):
    name: str

    def tools(self) -> list[ToolSpec]:
        """Return the list of tools this integration provides."""
        ...

    async def start(self) -> None:
        """Called at daemon startup. Connect to the service."""
        ...

    async def stop(self) -> None:
        """Called at daemon shutdown. Disconnect cleanly."""
        ...
```

## Planned Integrations (Phase 2+)

### Gmail
- Would add `read_email`, `search_email`, `send_email` tools
- All email content is `PRIVATE` — never sent to cloud
- Read tools are `read` tier; send is `write` tier

### Social Media Posting
- Would add platform-specific posting tools
- All `write` tier (approval required)
- Post content shown in the approval card before sending

### Calendar
- Would add `list_events`, `create_event`, `update_event` tools
- Calendar data is `PRIVATE`

## How to Add an Integration

1. Create a module in `noxai/integrations/<name>.py`
2. Implement the `Integration` protocol
3. Register it in `noxai/integrations/__init__.py`
4. The daemon's startup sequence will call `start()` automatically

All tools provided by integrations are subject to the same safety rules as
built-in tools. The tier you declare is the minimum — the safety layer may
escalate it based on taint context.
