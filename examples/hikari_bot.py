"""Discord history search for Claude in a plain hikari bot, without a command framework.

Install:  pip install "zerya-discord-mcp[mcp]" uvicorn
Run:      DISCORD_BOT_TOKEN=... ZDM_DB_PATH=zdm.sqlite3 ZDM_GUILD_ID=... ZDM_PUBLIC_URL=https://mcp.example.com \
          python hikari_bot.py

The same as arc_bot.py, wired by hand: the bot starts and stops the library, registers /mcp and /mcp-setup
through REST and answers them from raw interactions.
"""

import os
import threading

import hikari
import uvicorn

from zerya_discord_mcp import REQUIRED_INTENTS, Config, ZeryaDiscordMcp, upsert_commands
from zerya_discord_mcp.mcp import McpAsgiApp


def build(token: str, config: Config) -> tuple[hikari.GatewayBot, McpAsgiApp]:
    bot = hikari.GatewayBot(token, intents=hikari.Intents.ALL_UNPRIVILEGED | REQUIRED_INTENTS)
    zdm = ZeryaDiscordMcp(bot, config)

    @bot.listen(hikari.StartedEvent)
    async def on_started(_: hikari.StartedEvent) -> None:
        await zdm.start()
        await upsert_commands(bot.rest, config.guild_id)  # only /mcp and /mcp-setup, other commands stay
        await zdm.log_setup_report()

    @bot.listen(hikari.StoppingEvent)
    async def on_stopping(_: hikari.StoppingEvent) -> None:
        await zdm.stop()

    @bot.listen(hikari.InteractionCreateEvent)
    async def on_interaction(event: hikari.InteractionCreateEvent) -> None:
        interaction = event.interaction
        if not isinstance(interaction, hikari.CommandInteraction):
            return
        result = await zdm.commands.handle(interaction)
        if result is None:  # not one of ours
            return
        flags = hikari.MessageFlag.EPHEMERAL if result.ephemeral else hikari.MessageFlag.NONE
        await interaction.create_initial_response(
            hikari.ResponseType.MESSAGE_CREATE, embed=result.to_embed(), flags=flags
        )

    return bot, McpAsgiApp(zdm)


def main() -> None:
    bot, app = build(os.environ["DISCORD_BOT_TOKEN"], Config())  # every ZDM_* setting from the environment
    threading.Thread(target=uvicorn.run, args=(app,), kwargs={"port": 8000}, daemon=True).start()
    bot.run()


if __name__ == "__main__":
    main()
