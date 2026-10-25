"""`ragnarok` command line. Thin wrappers only: real logic lives in the packages."""

from __future__ import annotations

import typer

from ragnarok import __version__

app = typer.Typer(add_completion=False, no_args_is_help=True, help="Ragnarok: a sovereign personal AI agent.")


@app.command()
def version() -> None:
    """Print the version."""
    typer.echo(__version__)


@app.command()
def doctor(
    benchmark: bool = typer.Option(False, "--benchmark", help="Measure this machine and write profile.toml"),
    restore_test: bool = typer.Option(False, "--restore-test", help="Also run a backup restore test"),
) -> None:
    """Check this machine. Run after every pull, on every machine."""
    from ragnarok.doctor import run_doctor

    raise typer.Exit(run_doctor(benchmark=benchmark, restore_test=restore_test))


connect_app = typer.Typer(help="Connect a source. Tokens go to the OS keychain, never to a file.", no_args_is_help=True)
app.add_typer(connect_app, name="connect")


def _prompt_token(label: str) -> str:
    import getpass

    return getpass.getpass(f"{label} (input hidden): ").strip()


@connect_app.command("slack")
def connect_slack() -> None:
    """Store the Slack bot token (xoxb-...) and check it."""
    _store_and_check("slack", _prompt_token("Slack bot token"))


@connect_app.command("telegram")
def connect_telegram() -> None:
    """Store the Telegram bot token and check it."""
    _store_and_check("telegram", _prompt_token("Telegram bot token"))


@connect_app.command("discord")
def connect_discord() -> None:
    """Store the Discord bot token and check it."""
    _store_and_check("discord", _prompt_token("Discord bot token"))


def _store_and_check(name: str, token: str) -> None:
    import asyncio

    from ragnarok.connectors.registry import build
    from ragnarok.platform.secrets import default_store, token_name

    if not token:
        raise typer.Exit(1)
    default_store().set(token_name(name, "read"), token)
    health = asyncio.run(build(name, default_store()).health())
    typer.echo(f"{name}: {'connected' if health.ok else 'saved, but the check failed: ' + health.detail}")
    raise typer.Exit(0 if health.ok else 1)


@connect_app.command("google")
def connect_google(
    client_file: str = typer.Option(..., "--client-file", help="The Desktop-app OAuth client JSON from Google Cloud Console"),
    write: bool = typer.Option(False, "--write", help="Second sign-in for sending mail, writing events and files (separate token)"),
) -> None:
    """Sign in to Google once (Gmail, Calendar, Drive, Meet; read-only)."""
    from pathlib import Path

    from ragnarok.connectors.oauth_google import load_client_file, sign_in
    from ragnarok.platform.secrets import default_store

    cid, secret = load_client_file(Path(client_file).expanduser())
    sign_in(cid, secret, default_store(), write=write)
    typer.echo(f"google: {'write access' if write else 'read access'} connected (token is in the keychain; the JSON file can be deleted)")


@connect_app.command("outlook")
def connect_outlook(client_id: str = typer.Option(..., "--client-id", help="Application (client) ID from the Entra app registration")) -> None:
    """Sign in to Outlook mail and calendar (read-only) with a device code."""
    import asyncio
    import json
    import time

    from ragnarok.connectors.outlook import device_code_poll, device_code_start
    from ragnarok.platform.secrets import default_store, token_name

    async def run() -> str:
        d = await device_code_start(client_id)
        if "device_code" not in d:
            raise typer.Exit(typer.echo(f"outlook: {d.get('error_description', 'could not start sign-in')[:200]}", err=True) or 1)
        typer.echo(d["message"])
        deadline = time.monotonic() + int(d.get("expires_in", 900))
        while time.monotonic() < deadline:
            await asyncio.sleep(int(d.get("interval", 5)))
            rt = await device_code_poll(client_id, d["device_code"])
            if rt:
                return rt
        raise typer.Exit(typer.echo("outlook: sign-in timed out", err=True) or 1)

    rt = asyncio.run(run())
    default_store().set(token_name("outlook", "read"), json.dumps({"client_id": client_id, "refresh_token": rt}))
    typer.echo("outlook: connected (refresh token is in the keychain)")


@app.command()
def check() -> None:
    """Lint and run the fast tests (the pre-push gate)."""
    import subprocess
    import sys

    steps = [
        [sys.executable, "-m", "ruff", "check", "src", "tests"],
        [sys.executable, "-m", "pytest"],
    ]
    for cmd in steps:
        code = subprocess.call(cmd)  # noqa: S603
        if code != 0:
            raise typer.Exit(code)
    typer.echo("check: ok")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
