import logging

import arc
import hikari

from zerya_discord_mcp.commands import SETUP_PERMISSIONS, CommandResult, description, description_localizations
from zerya_discord_mcp.core import ZeryaDiscordMcp

logger = logging.getLogger("zerya_discord_mcp.arc")


async def _respond(ctx: arc.GatewayContext, result: CommandResult) -> None:
    flags = hikari.MessageFlag.EPHEMERAL if result.ephemeral else hikari.MessageFlag.NONE
    await ctx.respond(embed=result.to_embed(), flags=flags)


class McpPlugin(arc.GatewayPlugin):
    """Adds the library to an arc ``GatewayClient``: ``client.add_plugin(McpPlugin(zdm))``.

    It starts the library on StartedEvent (and logs the setup checklist), stops it on StoppingEvent, and adds
    /mcp login | list | revoke and /mcp-setup, registered with the client's command sync. Pass
    ``manage_lifecycle=False`` to call ``zdm.start()`` and ``zdm.stop()`` yourself, e.g. to order them against
    other startup work or to shut down when the library fails to start.
    """

    def __init__(self, zdm: ZeryaDiscordMcp, *, manage_lifecycle: bool = True) -> None:
        super().__init__("zerya_discord_mcp")
        self.zdm = zdm
        self._include_commands()
        if manage_lifecycle:
            self.subscribe(hikari.StartedEvent, self._on_started)
            self.subscribe(hikari.StoppingEvent, self._on_stopping)

    async def _on_started(self, _: hikari.StartedEvent) -> None:
        try:
            await self.zdm.start()
        except Exception:
            logger.exception("zerya-discord-mcp failed to start; search and sign-in are unavailable")
            return
        await self.zdm.log_setup_report()

    async def _on_stopping(self, _: hikari.StoppingEvent) -> None:
        await self.zdm.stop()

    def _include_commands(self) -> None:
        commands = self.zdm.commands
        group = self.include_slash_group(
            "mcp", description("mcp"), description_localizations=description_localizations("mcp")
        )

        @group.include
        @arc.slash_subcommand(
            "login", description("login"), description_localizations=description_localizations("login")
        )
        async def login(ctx: arc.GatewayContext) -> None:
            await _respond(ctx, await commands.login(int(ctx.user.id), ctx.locale))

        @group.include
        @arc.slash_subcommand("list", description("list"), description_localizations=description_localizations("list"))
        async def list_clients(ctx: arc.GatewayContext) -> None:
            await _respond(ctx, await commands.list(int(ctx.user.id), ctx.locale))

        @group.include
        @arc.slash_subcommand(
            "revoke", description("revoke"), description_localizations=description_localizations("revoke")
        )
        async def revoke(
            ctx: arc.GatewayContext,
            target: arc.Option[
                str, arc.StrParams(description("target"), description_localizations=description_localizations("target"))
            ],
        ) -> None:
            await _respond(ctx, await commands.revoke(int(ctx.user.id), target, ctx.locale))

        @self.include
        @arc.slash_command(
            "mcp-setup",
            description("mcp-setup"),
            description_localizations=description_localizations("mcp-setup"),
            default_permissions=SETUP_PERMISSIONS,
        )
        async def setup(ctx: arc.GatewayContext) -> None:
            await ctx.defer(flags=hikari.MessageFlag.EPHEMERAL)
            permissions = ctx.member.permissions if ctx.member else hikari.Permissions.NONE
            await _respond(ctx, await commands.setup(permissions, ctx.locale))
