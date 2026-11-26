"""Discord history search for Claude in a hikari bot that uses arc.

Install:  pip install "zerya-discord-mcp[arc,mcp]" uvicorn
Run:      DISCORD_BOT_TOKEN=... ZDM_DB_PATH=zdm.sqlite3 ZDM_GUILD_ID=... ZDM_PUBLIC_URL=https://mcp.example.com \
          python arc_bot.py

Route ZDM_PUBLIC_URL (HTTPS, origin root) to port 8000. Members run /mcp login on Discord and add
<ZDM_PUBLIC_URL>/mcp to Claude as a custom connector.
"""

import os
import threading

import arc
import hikari
import uvicorn

from zerya_discord_mcp import REQUIRED_INTENTS, Config, ZeryaDiscordMcp
from zerya_discord_mcp.arc import McpPlugin
from zerya_discord_mcp.mcp import McpAsgiApp


def build(token: str, config: Config) -> tuple[hikari.GatewayBot, arc.GatewayClient, McpAsgiApp]:
    bot = hikari.GatewayBot(token, intents=hikari.Intents.ALL_UNPRIVILEGED | REQUIRED_INTENTS)
    zdm = ZeryaDiscordMcp(bot, config)
    client = arc.GatewayClient(bot, default_enabled_guilds=[config.guild_id])
    # Starts and stops the library with the bot and adds /mcp login | list | revoke and /mcp-setup.
    client.add_plugin(McpPlugin(zdm))
    # MCP and OAuth sign-in for agents. Serve it at the root of ZDM_PUBLIC_URL.
    return bot, client, McpAsgiApp(zdm)


def main() -> None:
    bot, _, app = build(os.environ["DISCORD_BOT_TOKEN"], Config())  # every ZDM_* setting from the environment
    # The app works from any thread: calls into Discord hop to the bot's event loop.
    threading.Thread(target=uvicorn.run, args=(app,), kwargs={"port": 8000}, daemon=True).start()
    bot.run()


if __name__ == "__main__":
    main()
