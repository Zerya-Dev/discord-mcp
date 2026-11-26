"""The library as a remote MCP server with its own OAuth sign-in. Needs the ``[mcp]`` extra."""

from zerya_discord_mcp._extras import require_extra

require_extra("mcp", "mcp", "starlette")

from zerya_discord_mcp.mcp._app import McpAsgiApp  # noqa: E402  (after the extra check, for its error message)
from zerya_discord_mcp.mcp._principal import Authenticate, Principal  # noqa: E402

__all__ = ["Authenticate", "McpAsgiApp", "Principal"]
