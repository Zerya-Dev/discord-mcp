"""Setup checks a server owner can act on (logged at startup and shown by /mcp setup)."""

from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from zerya_discord_mcp.access.permissions import (
    MANAGE_THREADS,
    READ_MESSAGE_HISTORY,
    SEND_MESSAGES,
    SEND_MESSAGES_IN_THREADS,
    VIEW_CHANNEL,
    GuildAcl,
    base_permissions,
)

if TYPE_CHECKING:
    from zerya_discord_mcp.core import ZeryaDiscordMcp


@dataclass(frozen=True, slots=True)
class SetupCheck:
    ok: bool
    label: str
    detail: str = ""
    # Problems that block the core feature (search) are errors; the rest are warnings.
    required: bool = True

    def __str__(self) -> str:
        mark = "OK" if self.ok else ("FAIL" if self.required else "WARN")
        return f"[{mark}] {self.label}" + (f" — {self.detail}" if self.detail else "")


_PERMISSION_NAMES = {
    VIEW_CHANNEL: "View Channels",
    READ_MESSAGE_HISTORY: "Read Message History",
    SEND_MESSAGES: "Send Messages",
    SEND_MESSAGES_IN_THREADS: "Send Messages in Threads",
    MANAGE_THREADS: "Manage Threads (to index private threads)",
}


def required_permissions(write_enabled: bool) -> int:
    perms = VIEW_CHANNEL | READ_MESSAGE_HISTORY | MANAGE_THREADS
    if write_enabled:
        perms |= SEND_MESSAGES | SEND_MESSAGES_IN_THREADS
    return perms


def invite_url(application_id: int, write_enabled: bool) -> str:
    return (
        f"https://discord.com/oauth2/authorize?client_id={application_id}"
        f"&scope=bot+applications.commands&permissions={required_permissions(write_enabled)}"
    )


async def run_setup_checks(core: ZeryaDiscordMcp) -> list[SetupCheck]:
    config = core.config
    bot = core.bot
    checks: list[SetupCheck] = []
    me = bot.get_me()
    application_id = int(me.id) if me else 0

    guild = bot.cache.get_guild(config.guild_id)
    if guild is None:
        checks.append(
            SetupCheck(
                False,
                "Bot is on the server",
                f"guild {config.guild_id} not found; invite it: {invite_url(application_id, config.write_enabled)}",
            )
        )
        return checks
    checks.append(SetupCheck(True, "Bot is on the server", guild.name))

    member = bot.cache.get_member(config.guild_id, application_id) if me else None
    if member is not None:
        roles = {int(rid): int(r.permissions) for rid, r in bot.cache.get_roles_view_for_guild(config.guild_id).items()}
        acl = GuildAcl(guild_id=config.guild_id, owner_id=int(guild.owner_id), role_permissions=roles, channels={})
        have = base_permissions(acl, application_id, [int(r) for r in member.role_ids])
        for bit, name in _PERMISSION_NAMES.items():
            if not bit & required_permissions(config.write_enabled):
                continue
            optional = bit == MANAGE_THREADS
            checks.append(
                SetupCheck(
                    bool(have & bit),
                    f"Permission: {name}",
                    "" if have & bit else "grant it to the bot's role (server-wide or per channel)",
                    required=not optional,
                )
            )

    url = config.public_url
    if not url:
        checks.append(SetupCheck(False, "Public URL", "ZDM_PUBLIC_URL is not set: Claude cannot connect"))
    else:
        parsed = urlparse(url)
        secure = parsed.scheme == "https" and parsed.port in (None, 443)
        checks.append(
            SetupCheck(
                secure,
                "Public URL",
                f"{url}/mcp" if secure else f"{url} must be HTTPS on port 443 for claude.ai",
                required=False,
            )
        )

    backfill = core.backfill
    if backfill is not None and backfill.done.is_set():
        detail = f"{backfill.indexed_messages} new messages"
        if backfill.failed_channels:
            detail += f", {backfill.failed_channels} channels failed (see logs)"
        checks.append(SetupCheck(backfill.failed_channels == 0, "History indexed", detail, required=False))
    else:
        checks.append(SetupCheck(True, "History indexing", "in progress", required=False))

    checks.append(
        SetupCheck(
            True,
            "Writing (send)",
            "enabled" if config.write_enabled else "disabled (read-only, ZDM_WRITE_ENABLED=false)",
            required=False,
        )
    )
    return checks
