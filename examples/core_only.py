"""Permission-aware history search inside your own bot, without MCP or arc.

Install:  pip install zerya-discord-mcp
Run:      DISCORD_BOT_TOKEN=... ZDM_DB_PATH=zdm.sqlite3 ZDM_GUILD_ID=... python core_only.py

The library indexes the server; your code calls the same tools agents use, as a given member. Here a member
writes "!search logo fundacja" and gets back what they themselves can read.
"""

import os

import hikari

from zerya_discord_mcp import REQUIRED_INTENTS, AccessDeniedError, Config, ZeryaDiscordMcp

PREFIX = "!search "


async def answer(zdm: ZeryaDiscordMcp, user_id: int, content: str) -> str | None:
    """The reply to a message, or None when it is not a search."""
    if not content.startswith(PREFIX):
        return None
    try:
        text = await zdm.tools.search(user_id, content.removeprefix(PREFIX))
    except AccessDeniedError:
        return "You are not a member of the server."
    return text[:1900]


def build(token: str, config: Config) -> tuple[hikari.GatewayBot, ZeryaDiscordMcp]:
    bot = hikari.GatewayBot(token, intents=hikari.Intents.ALL_UNPRIVILEGED | REQUIRED_INTENTS)
    zdm = ZeryaDiscordMcp(bot, config)

    @bot.listen(hikari.StartedEvent)
    async def on_started(_: hikari.StartedEvent) -> None:
        await zdm.start()
        await zdm.log_setup_report()

    @bot.listen(hikari.StoppingEvent)
    async def on_stopping(_: hikari.StoppingEvent) -> None:
        await zdm.stop()

    @bot.listen(hikari.GuildMessageCreateEvent)
    async def on_message(event: hikari.GuildMessageCreateEvent) -> None:
        reply = await answer(zdm, int(event.author_id), event.content or "")
        if reply is not None:
            await event.message.respond(reply, reply=True, user_mentions=False, role_mentions=False)

    return bot, zdm


def main() -> None:
    bot, _ = build(os.environ["DISCORD_BOT_TOKEN"], Config())  # every ZDM_* setting from the environment
    bot.run()


if __name__ == "__main__":
    main()
