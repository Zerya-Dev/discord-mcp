import hikari
import pytest

from zerya_discord_mcp_bot.commands import DESCRIPTIONS, command_builders


@pytest.fixture
def builders() -> dict[str, hikari.api.SlashCommandBuilder]:
    bot = hikari.GatewayBot(token="MTIz.fake.token", banner=None)  # never started: no network
    return {b.name: b for b in command_builders(bot)}


def _polish(name: str) -> dict[hikari.Locale | str, str]:
    return {hikari.Locale.PL: DESCRIPTIONS[name][1]}


def test_only_mcp_and_mcp_setup_are_built(builders: dict[str, hikari.api.SlashCommandBuilder]) -> None:
    assert set(builders) == {"mcp", "mcp-setup"}


def test_mcp_has_login_list_and_revoke_with_a_required_target(
    builders: dict[str, hikari.api.SlashCommandBuilder],
) -> None:
    mcp = builders["mcp"]
    assert mcp.description == DESCRIPTIONS["mcp"][0]
    assert mcp.description_localizations == _polish("mcp")
    assert mcp.default_member_permissions is hikari.UNDEFINED  # every member may connect

    subcommands = {o.name: o for o in mcp.options}
    assert list(subcommands) == ["login", "list", "revoke"]
    for name, option in subcommands.items():
        assert option.type is hikari.OptionType.SUB_COMMAND
        assert option.description == DESCRIPTIONS[name][0]
        assert option.description_localizations == _polish(name)
    assert subcommands["login"].options is None and subcommands["list"].options is None

    (target,) = subcommands["revoke"].options or []
    assert (target.name, target.type, target.is_required) == ("target", hikari.OptionType.STRING, True)
    assert target.description_localizations == _polish("target")


def test_mcp_setup_is_for_server_managers(builders: dict[str, hikari.api.SlashCommandBuilder]) -> None:
    setup = builders["mcp-setup"]
    assert setup.default_member_permissions == hikari.Permissions.MANAGE_GUILD
    assert setup.description_localizations == _polish("mcp-setup")
    assert setup.options == []


def test_every_description_fits_discord_limits() -> None:
    for name, (english, polish) in DESCRIPTIONS.items():
        assert 1 <= len(english) <= 100 and 1 <= len(polish) <= 100, name
