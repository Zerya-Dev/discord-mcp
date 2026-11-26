"""/mcp login | list | revoke and /mcp-setup, independent of any command framework.

``command_builders()`` defines the commands (names, options, English and Polish descriptions) and ``McpCommands``
runs them. Results are framework-neutral (``CommandResult``); ``to_embed()`` renders the usual Discord embed. Pass
the interaction locale to get Polish or English texts. ``zerya_discord_mcp.arc.McpPlugin`` wires both into arc.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import hikari

from zerya_discord_mcp.access.grants import SCOPE_WRITE, GrantStore
from zerya_discord_mcp.config import Config
from zerya_discord_mcp.diagnostics import SetupCheck
from zerya_discord_mcp.i18n import Lang, from_locale, t

# name: (English description, Polish description)
DESCRIPTIONS = {
    "mcp": ("Connect Claude and other AI agents to this server's history", "Podłącz Claude i agentów AI do historii"),
    "login": ("Get a one-time code to connect Claude", "Jednorazowy kod do połączenia Claude"),
    "list": ("List AI clients connected to your account", "Lista klientów AI podłączonych do Twojego konta"),
    "revoke": ("Disconnect an AI client", "Odłącz klienta AI"),
    "target": ("Connection ID from /mcp list, or all", "ID połączenia z /mcp list albo all"),
    "mcp-setup": ("Check the MCP setup of this server (admins)", "Sprawdź konfigurację MCP serwera (admini)"),
}
# /mcp-setup is for server managers; the handler checks again in case a server overrides the default.
SETUP_PERMISSIONS = hikari.Permissions.MANAGE_GUILD


def description(name: str) -> str:
    return DESCRIPTIONS[name][0]


def description_localizations(name: str) -> dict[hikari.Locale, str]:
    return {hikari.Locale.PL: DESCRIPTIONS[name][1]}


def _rest_localizations(name: str) -> dict[hikari.Locale | str, str]:
    """The same localizations, typed for hikari's REST builders (which also accept locale strings)."""
    return {**description_localizations(name)}


def _option(
    name: str, option_type: hikari.OptionType, **kwargs: bool | list[hikari.CommandOption]
) -> hikari.CommandOption:
    return hikari.CommandOption(
        type=option_type,
        name=name,
        description=description(name),
        description_localizations=_rest_localizations(name),
        **kwargs,
    )


def command_builders(rest: hikari.api.RESTClient) -> list[hikari.api.SlashCommandBuilder]:
    """/mcp (login, list, revoke <target>) and /mcp-setup, for hosts that register commands through REST."""
    sub, text = hikari.OptionType.SUB_COMMAND, hikari.OptionType.STRING
    mcp = rest.slash_command_builder("mcp", description("mcp")).set_description_localizations(
        _rest_localizations("mcp")
    )
    mcp.add_option(_option("login", sub))
    mcp.add_option(_option("list", sub))
    mcp.add_option(_option("revoke", sub, options=[_option("target", text, is_required=True)]))
    setup = (
        rest
        .slash_command_builder("mcp-setup", description("mcp-setup"))
        .set_description_localizations(_rest_localizations("mcp-setup"))
        .set_default_member_permissions(SETUP_PERMISSIONS)
    )
    return [mcp, setup]


async def upsert_commands(rest: hikari.api.RESTClient, guild_id: int) -> None:
    """Creates or updates only /mcp and /mcp-setup in the guild, leaving every other command alone.

    Discord treats a create with an existing name as an update, so this is safe to run on every start.
    """
    application = await rest.fetch_application()
    for builder in command_builders(rest):
        await builder.create(rest, application, guild=guild_id)


# Zerya brand: forest is the brand colour, ember the single point of attention for problems.
COLOR_INFO = 0x0E3B34
COLOR_OK = 0x0E3B34
COLOR_WARN = 0xF1E15B
COLOR_ERROR = 0xF1E15B


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
        grants: GrantStore,
        config: Config,
        is_member: Callable[[int], bool],
        setup_report: Callable[[], Awaitable[list[SetupCheck]]],
        server_name: Callable[[], str] = lambda: "Discord",
    ) -> None:
        self.grants = grants
        self.config = config
        self.is_member = is_member
        self.setup_report = setup_report
        self.server_name = server_name

    async def handle(self, interaction: hikari.CommandInteraction) -> CommandResult | None:
        """Runs /mcp or /mcp-setup from a raw interaction; None when the interaction is another command.

        For hosts without a command framework: register ``command_builders()`` and call this from
        ``InteractionCreateEvent``.
        """
        locale = str(interaction.locale)
        user_id = int(interaction.user.id)
        if interaction.command_name == "mcp-setup":
            member = interaction.member
            return await self.setup(member.permissions if member else hikari.Permissions.NONE, locale)
        if interaction.command_name != "mcp" or not interaction.options:
            return None
        subcommand = interaction.options[0]
        match subcommand.name:
            case "login":
                return await self.login(user_id, locale)
            case "list":
                return await self.list(user_id, locale)
            case "revoke":
                target = next((str(o.value) for o in subcommand.options or [] if o.name == "target"), "")
                return await self.revoke(user_id, target, locale)
            case _:
                return None

    async def login(self, user_id: int, locale: str | None = None) -> CommandResult:
        lang = from_locale(locale)
        if not self.is_member(user_id):
            return CommandResult(t("error.title", lang), t("login.not_member", lang), color=COLOR_ERROR)
        code, expires = await asyncio.to_thread(self.grants.create_login_code, user_id)
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
        grants = await asyncio.to_thread(self.grants.list_grants, user_id)
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
        count = await asyncio.to_thread(self.grants.revoke_user_grants, user_id, grant_id)
        if count == 0:
            return CommandResult(t("revoke.none_title", lang), t("revoke.none", lang, target=target), color=COLOR_ERROR)
        return CommandResult(t("revoke.done_title", lang), t("revoke.done", lang, count=count), color=COLOR_OK)

    async def setup(self, permissions: hikari.Permissions, locale: str | None = None) -> CommandResult:
        """Setup checklist for server managers. ``permissions`` are the invoking member's resolved permissions.

        Registering the command with ``SETUP_PERMISSIONS`` only hides it; a server can override that, so the
        permissions are checked here too.
        """
        lang = from_locale(locale)
        if not permissions & (SETUP_PERMISSIONS | hikari.Permissions.ADMINISTRATOR):
            return CommandResult(t("error.title", lang), t("setup.forbidden", lang), color=COLOR_ERROR)
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
