from importlib.util import find_spec


def require_extra(extra: str, *modules: str) -> None:
    """Fails an integration's import with the install hint when its optional dependencies are missing."""
    missing = [m for m in modules if find_spec(m) is None]
    if missing:
        raise ImportError(
            f"zerya_discord_mcp.{extra} needs the [{extra}] extra ({', '.join(missing)} not installed): "
            f'pip install "zerya-discord-mcp[{extra}]"'
        )
