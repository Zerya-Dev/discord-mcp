"""The library as an arc plugin. Needs the ``[arc]`` extra."""

from zerya_discord_mcp._extras import require_extra

require_extra("arc", "arc")

from zerya_discord_mcp.arc._plugin import McpPlugin  # noqa: E402  (after the extra check, for its error message)

__all__ = ["McpPlugin"]
