"""/mcp login | list | revoke and /mcp-setup as an arc plugin: a thin adapter over ``ZeryaDiscordMcp.commands``."""

import arc
import hikari

from zerya_discord_mcp import CommandResult, ZeryaDiscordMcp

PL = hikari.Locale.PL

# name: (English description, Polish description)
DESCRIPTIONS = {
    "mcp": ("Connect Claude and other AI agents to this server's history", "Podłącz Claude i agentów AI do historii"),
    "login": ("Get a one-time code to connect Claude", "Jednorazowy kod do połączenia Claude"),
    "list": ("List AI clients connected to your account", "Lista klientów AI podłączonych do Twojego konta"),
    "revoke": ("Disconnect an AI client", "Odłącz klienta AI"),
    "target": ("Connection ID from /mcp list, or all", "ID połączenia z /mcp list albo all"),
    "mcp-setup": ("Check the MCP setup of this server (admins)", "Sprawdź konfigurację MCP serwera (admini)"),
}


def _en(name: str) -> str:
    return DESCRIPTIONS[name][0]


def _pl(name: str) -> dict[hikari.Locale, str]:
    return {PL: DESCRIPTIONS[name][1]}


def _pl_rest(name: str) -> dict[hikari.Locale | str, str]:
    """Same as _pl, typed for hikari's REST builders."""
    return {PL: DESCRIPTIONS[name][1]}


async def _respond(ctx: arc.GatewayContext, result: CommandResult) -> None:
    flags = hikari.MessageFlag.EPHEMERAL if result.ephemeral else hikari.MessageFlag.NONE
    await ctx.respond(embed=result.to_embed(), flags=flags)


def build_plugin(zdm: ZeryaDiscordMcp) -> arc.GatewayPlugin:
    plugin = arc.GatewayPlugin("zerya_discord_mcp")
    group = plugin.include_slash_group("mcp", _en("mcp"), description_localizations=_pl("mcp"))

    @group.include
    @arc.slash_subcommand("login", _en("login"), description_localizations=_pl("login"))
    async def login(ctx: arc.GatewayContext) -> None:
        await _respond(ctx, await zdm.commands.login(int(ctx.user.id), ctx.locale))

    @group.include
    @arc.slash_subcommand("list", _en("list"), description_localizations=_pl("list"))
    async def list_clients(ctx: arc.GatewayContext) -> None:
        await _respond(ctx, await zdm.commands.list(int(ctx.user.id), ctx.locale))

    @group.include
    @arc.slash_subcommand("revoke", _en("revoke"), description_localizations=_pl("revoke"))
    async def revoke(
        ctx: arc.GatewayContext,
        target: arc.Option[str, arc.StrParams(_en("target"), description_localizations=_pl("target"))],
    ) -> None:
        await _respond(ctx, await zdm.commands.revoke(int(ctx.user.id), target, ctx.locale))

    @plugin.include
    @arc.slash_command(
        "mcp-setup",
        _en("mcp-setup"),
        description_localizations=_pl("mcp-setup"),
        default_permissions=hikari.Permissions.MANAGE_GUILD,
    )
    async def setup(ctx: arc.GatewayContext) -> None:
        # default_permissions only hides the command; check again in case a server overrides it.
        permissions = ctx.member.permissions if ctx.member else hikari.Permissions.NONE
        if not permissions & (hikari.Permissions.MANAGE_GUILD | hikari.Permissions.ADMINISTRATOR):
            await ctx.respond("Manage Server permission required.", flags=hikari.MessageFlag.EPHEMERAL)
            return
        await ctx.defer(flags=hikari.MessageFlag.EPHEMERAL)
        await _respond(ctx, await zdm.commands.setup(ctx.locale))

    return plugin


def _option(
    name: str, option_type: hikari.OptionType, **kwargs: bool | list[hikari.CommandOption]
) -> hikari.CommandOption:
    return hikari.CommandOption(
        type=option_type,
        name=name,
        description=_en(name),
        description_localizations=_pl_rest(name),
        **kwargs,
    )


def command_builders(bot: hikari.GatewayBot) -> list[hikari.api.SlashCommandBuilder]:
    sub, text = hikari.OptionType.SUB_COMMAND, hikari.OptionType.STRING
    mcp = bot.rest.slash_command_builder("mcp", _en("mcp")).set_description_localizations(_pl_rest("mcp"))
    mcp.add_option(_option("login", sub))
    mcp.add_option(_option("list", sub))
    mcp.add_option(_option("revoke", sub, options=[_option("target", text, is_required=True)]))
    setup = (
        bot.rest
        .slash_command_builder("mcp-setup", _en("mcp-setup"))
        .set_description_localizations(_pl_rest("mcp-setup"))
        .set_default_member_permissions(hikari.Permissions.MANAGE_GUILD)
    )
    return [mcp, setup]


async def upsert_mcp_command(bot: hikari.GatewayBot, guild_id: int) -> None:
    """Creates or updates only /mcp and /mcp-setup in the guild, leaving every other command alone.

    Discord treats a create with an existing name as an update, so this is safe to run on every start.
    """
    application = await bot.rest.fetch_application()
    for builder in command_builders(bot):
        await builder.create(bot.rest, application, guild=guild_id)
