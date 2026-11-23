"""Fast, permission-aware search over Discord history for AI agents (MCP).

Public API: only the names exported here are stable between versions.
"""

from zerya_discord_mcp.commands import CommandResult
from zerya_discord_mcp.config import Config
from zerya_discord_mcp.core import AccessDeniedError, Tools, ZeryaDiscordMcp

__all__ = ["AccessDeniedError", "CommandResult", "Config", "Tools", "ZeryaDiscordMcp"]
