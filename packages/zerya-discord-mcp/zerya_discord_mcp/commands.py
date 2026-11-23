"""Logic of /mcp login | list | revoke | setup. Hosts register the slash command in their framework and call these.

Results are framework-neutral (``CommandResult``); ``to_embed()`` renders the usual Discord embed. Pass the
interaction locale to get Polish or English texts.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import hikari

from zerya_discord_mcp.access.oauth import SCOPE_WRITE, OAuthStore
from zerya_discord_mcp.config import Config
from zerya_discord_mcp.diagnostics import SetupCheck
from zerya_discord_mcp.i18n import Lang, from_locale, t

COLOR_INFO = 0x5B5BD6
COLOR_OK = 0x2F9E6E
COLOR_WARN = 0xE0A100
COLOR_ERROR = 0xD9534F


@dataclass(frozen=True, slots=True)
class CommandResult:
    title: str
    description: str = ""
    fields: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])
    footer: str | None = None
    color: int = COLOR_INFO
    ephemeral: bool = True

    @property
    def content(self) -> str:
        """Plain-text version for hosts that do not use embeds."""
        parts = [f"**{self.title}**", self.description, *(f"**{n}**\n{v}" for n, v in self.fields)]
        if self.footer:
            parts.append(f"-# {self.footer}")
        return "\n".join(p for p in parts if p)

    def to_embed(self) -> hikari.Embed:
        embed = hikari.Embed(title=self.title, description=self.description or None, color=self.color)
        for name, value in self.fields:
            embed.add_field(name=name, value=value)
        if self.footer:
            embed.set_footer(self.footer)
        return embed


def _ts(ts: int | None, lang: Lang, style: str = "R") -> str:
    return f"<t:{ts}:{style}>" if ts else t("list.never", lang)


class McpCommands:
    def __init__(
        self,
        oauth: OAuthStore,
        config: Config,
        is_member: Callable[[int], bool],
        setup_report: Callable[[], Awaitable[list[SetupCheck]]],
        server_name: Callable[[], str] = lambda: "Discord",
    ) -> None:
        self.oauth = oauth
        self.config = config
        self.is_member = is_member
        self.setup_report = setup_report
        self.server_name = server_name

    async def login(self, user_id: int, locale: str | None = None) -> CommandResult:
        lang = from_locale(locale)
        if not self.is_member(user_id):
            return CommandResult(t("error.title", lang), t("login.not_member", lang), color=COLOR_ERROR)
        code, expires = await asyncio.to_thread(self.oauth.create_login_code, user_id)
        fields = [
            (t("login.valid", lang), t("login.valid_value", lang, expires=_ts(expires, lang))),
            (t("login.how", lang), t("login.how_value", lang, server=self.server_name())),
        ]
        if self.config.public_url:
            url = f"{self.config.public_url}/mcp"
            fields.append((t("login.not_connected", lang), t("login.not_connected_value", lang, url=url)))
        access = t("access.read_write" if self.config.write_enabled else "access.read", lang)
        fields.append((t("login.sees", lang), t("login.sees_value", lang, access=access)))
        return CommandResult(
            title=t("login.title", lang),
            description=f"```\n{code}\n```",
            fields=fields,
            footer=t("login.footer", lang),
        )

    async def list(self, user_id: int, locale: str | None = None) -> CommandResult:
        lang = from_locale(locale)
        grants = await asyncio.to_thread(self.oauth.list_grants, user_id)
        active = [g for g in grants if g.active]
        if not active:
            return CommandResult(title=t("list.title", lang), description=t("list.empty", lang))
        fields: list[tuple[str, str]] = []
        for g in active[:20]:
            access = t("list.read_write" if SCOPE_WRITE in g.scopes else "list.read_only", lang)
            fields.append((
                f"{g.client_name or t('list.client', lang)} · `{g.id}`",
                t("list.entry", lang, access=access, created=_ts(g.created_at, lang), used=_ts(g.last_used_at, lang)),
            ))
        return CommandResult(title=t("list.title", lang), fields=fields, footer=t("list.footer", lang))

    async def revoke(self, user_id: int, target: str, locale: str | None = None) -> CommandResult:
        lang = from_locale(locale)
        target = target.strip()
        if not target:
            return CommandResult(t("revoke.missing_title", lang), t("revoke.missing", lang), color=COLOR_ERROR)
        grant_id = None if target.lower() == "all" else target
        count = await asyncio.to_thread(self.oauth.revoke_user_grants, user_id, grant_id)
        if count == 0:
            return CommandResult(t("revoke.none_title", lang), t("revoke.none", lang, target=target), color=COLOR_ERROR)
        return CommandResult(t("revoke.done_title", lang), t("revoke.done", lang, count=count), color=COLOR_OK)

    async def setup(self, locale: str | None = None) -> CommandResult:
        """Setup checklist for server admins (the host should restrict it to Manage Server)."""
        lang = from_locale(locale)
        checks = await self.setup_report()
        failed = [c for c in checks if not c.ok and c.required]
        warned = [c for c in checks if not c.ok and not c.required]
        lines = []
        for check in checks:
            mark = "✅" if check.ok else ("❌" if check.required else "⚠️")
            lines.append(f"{mark} **{check.label}**" + (f" — {check.detail}" if check.detail else ""))
        summary = t("setup.problems", lang) if failed or warned else t("setup.ok", lang)
        color = COLOR_ERROR if failed else (COLOR_WARN if warned else COLOR_OK)
        return CommandResult(
            title=t("setup.title", lang, server=self.server_name()),
            description=summary + "\n\n" + "\n".join(lines),
            footer=t("setup.footer", lang),
            color=color,
        )
