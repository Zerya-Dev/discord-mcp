"""Builds one example in a clean environment: ``python -I check_example.py <example.py>``.

Run by test_install.py with the interpreter of a fresh virtualenv that has only the released wheel (with the
extras the example's docstring names) installed, so it proves the example works for a user, not for this checkout.
"""

import importlib.util
import sys
import tempfile
from pathlib import Path

from zerya_discord_mcp import Config


def main(path: str) -> None:
    spec = importlib.util.spec_from_file_location("example", path)
    assert spec is not None and spec.loader is not None
    example = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(example)
    with tempfile.TemporaryDirectory() as tmp:
        config = Config(db_path=Path(tmp) / "zdm.sqlite3", guild_id=1, public_url="https://mcp.example.com")
        built = example.build("MTIz.fake.token", config)
    print(f"ok: {Path(path).name} built {', '.join(type(part).__name__ for part in built)}")


if __name__ == "__main__":
    main(sys.argv[1])
