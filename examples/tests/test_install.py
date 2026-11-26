"""The built wheel, installed with each combination of extras into a clean virtualenv.

Regression tests for packaging: the base install must import without the optional dependencies (1.0.0 needed
``mcp`` for ``import zerya_discord_mcp``), an integration without its extra must fail with the install hint, and
every example must build with exactly the dependencies its docstring tells users to install.

Slow and needs the package index (or uv's cache): run with ``just test-install``.
"""

import subprocess
from pathlib import Path

import pytest

from examples.tests.examples import EXAMPLES

pytestmark = pytest.mark.install

ROOT = EXAMPLES.parent
CHECK = Path(__file__).resolve().parent / "check_example.py"


def _run(*args: str | Path) -> str:
    result = subprocess.run([str(a) for a in args], capture_output=True, text=True, check=False)
    assert result.returncode == 0, f"{' '.join(map(str, args))}\n{result.stdout}\n{result.stderr}"
    return result.stdout


@pytest.fixture(scope="session")
def wheel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("dist")
    _run("uv", "build", "--wheel", "--quiet", "--out-dir", out, ROOT / "packages" / "zerya-discord-mcp")
    (built,) = out.glob("zerya_discord_mcp-*.whl")
    return built


def _python(tmp_path: Path, wheel: Path, extras: str, *packages: str) -> Path:
    """A fresh virtualenv with the wheel (and ``extras``) plus ``packages``; returns its interpreter."""
    venv = tmp_path / "venv"
    _run("uv", "venv", "--quiet", "--python", "3.14", venv)
    python = venv / "bin" / "python"
    requirement = f"zerya-discord-mcp{f'[{extras}]' if extras else ''} @ {wheel.as_uri()}"
    _run("uv", "pip", "install", "--quiet", "--python", python, requirement, *packages)
    return python


def _import_error(python: Path, module: str) -> str:
    result = subprocess.run([str(python), "-I", "-c", f"import {module}"], capture_output=True, text=True, check=False)
    assert result.returncode != 0, f"{module} imported without its extra"
    return result.stderr


def test_base_install_imports_and_runs_core_only(tmp_path: Path, wheel: Path) -> None:
    python = _python(tmp_path, wheel, "")

    _run(python, "-I", "-c", "import zerya_discord_mcp")
    assert 'pip install "zerya-discord-mcp[arc]"' in _import_error(python, "zerya_discord_mcp.arc")
    assert 'pip install "zerya-discord-mcp[mcp]"' in _import_error(python, "zerya_discord_mcp.mcp")
    assert "ok: core_only.py" in _run(python, "-I", CHECK, EXAMPLES / "core_only.py")


def test_arc_extra_alone_gives_the_plugin(tmp_path: Path, wheel: Path) -> None:
    python = _python(tmp_path, wheel, "arc")

    _run(python, "-I", "-c", "from zerya_discord_mcp.arc import McpPlugin")
    assert 'pip install "zerya-discord-mcp[mcp]"' in _import_error(python, "zerya_discord_mcp.mcp")


def test_mcp_extra_runs_hikari_bot(tmp_path: Path, wheel: Path) -> None:
    python = _python(tmp_path, wheel, "mcp", "uvicorn")

    assert 'pip install "zerya-discord-mcp[arc]"' in _import_error(python, "zerya_discord_mcp.arc")
    assert "ok: hikari_bot.py" in _run(python, "-I", CHECK, EXAMPLES / "hikari_bot.py")


def test_arc_and_mcp_extras_run_arc_bot(tmp_path: Path, wheel: Path) -> None:
    python = _python(tmp_path, wheel, "arc,mcp", "uvicorn")

    assert "ok: arc_bot.py" in _run(python, "-I", CHECK, EXAMPLES / "arc_bot.py")
