"""Fast, permission-aware search over Discord history for AI agents (MCP).

Public API: only the names exported here, from ``zerya_discord_mcp.arc`` (``[arc]`` extra) and from
``zerya_discord_mcp.mcp`` (``[mcp]`` extra) are stable between versions.
"""

from zerya_discord_mcp.access.grants import Grant
from zerya_discord_mcp.commands import CommandResult, McpCommands, command_builders, upsert_commands
from zerya_discord_mcp.config import Config
from zerya_discord_mcp.core import REQUIRED_INTENTS, AccessDeniedError, Tools, ZeryaDiscordMcp
from zerya_discord_mcp.diagnostics import SetupCheck

__all__ = [
    "REQUIRED_INTENTS",
    "AccessDeniedError",
    "CommandResult",
    "Config",
    "Grant",
    "McpCommands",
    "SetupCheck",
    "Tools",
    "ZeryaDiscordMcp",
    "command_builders",
    "upsert_commands",
]
