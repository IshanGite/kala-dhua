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


skill_app = typer.Typer(help="Define and manage your own skills (a skill is a small program the agent may call, run in a sandbox).", no_args_is_help=True)
app.add_typer(skill_app, name="skill")

_MAIN = '''import json
import sys

req = json.load(sys.stdin)  # {"params": {...}, "ctx": {"workspace": ..., "dry_run": bool}}
params, ctx = req["params"], req["ctx"]
if ctx.get("dry_run"):
    print(json.dumps({"ok": True, "result": {"would_do": params}}))
    sys.exit(0)
# --- your code: it runs in a sandbox (no network, only the workspace folder) ---
result = {"echo": params}
print(json.dumps({"ok": True, "result": result}))
'''


@skill_app.command("new")
def skill_new(name: str = typer.Argument(..., help="lowercase name, for example text.count"), folder: str = typer.Option(".", help="where to create the skill folder"),
              permission: list[str] = typer.Option([], "--permission", help="what it needs: fs.read, fs.write, note.write, ... (default none)")) -> None:  # noqa: B008
    """Create a skill template you can edit, then register with `ragnarok skill add`."""
    import json
    from pathlib import Path

    d = Path(folder).expanduser() / name
    if d.exists():
        typer.echo(f"{d} already exists", err=True)
        raise typer.Exit(1)
    d.mkdir(parents=True)
    manifest = {"name": name, "version": "0.1.0", "description": f"Use when the user asks to {name.replace('.', ' ')}. (Describe, in one sentence, when to use this skill.)",
                "entrypoint": {"type": "python", "file": "main.py"}, "parameters": {"type": "object", "properties": {"text": {"type": "string", "maxLength": 5000}}, "required": ["text"], "additionalProperties": False},
                "permissions": [{"permission": p} for p in permission], "side_effects": "none", "dry_run": True, "examples": [{"params": {"text": "example"}, "note": "an example request in plain words"}]}
    (d / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (d / "main.py").write_text(_MAIN, encoding="utf-8")
    typer.echo(f"Created {d}. Edit main.py and manifest.json, then: ragnarok skill add {d}")


def _admin():  # noqa: ANN202
    from ragnarok.broker.approvals import Broker
    from ragnarok.config.paths import data_dir, db_path, ensure_dirs
    from ragnarok.memory.ledger import Ledger
    from ragnarok.memory.service import MemoryService
    from ragnarok.platform.secrets import default_store
    from ragnarok.skills.registry import SkillRegistry
    from ragnarok.store.crypto import KeyHierarchy

    root = ensure_dirs()
    keys = KeyHierarchy.load_or_create(default_store())
    mem = MemoryService(db_path(root), keys)
    ledger = Ledger(mem)
    return mem, ledger, SkillRegistry(ledger, Broker(ledger, keys), data_dir() / "skills")


@skill_app.command("add")
def skill_add(folder: str = typer.Argument(..., help="a folder with manifest.json and the skill's files")) -> None:
    """Register a skill: it is copied, scanned and hashed. It stays OFF until you approve it in the window (Sources > Skills)."""
    from pathlib import Path

    mem, _ledger, reg = _admin()
    try:
        r = reg.register(Path(folder).expanduser())
        typer.echo(f"registered {r.skill_id} (risk tier {int(r.manifest.tier)}); install scan: {'BLOCKED' if r.report.blocked else 'clean'}. Enable it in the window; it needs your step-up approval.")
    finally:
        mem.close()


@skill_app.command("list")
def skill_list() -> None:
    """List registered skills and whether they are on."""
    mem, ledger, _reg = _admin()
    try:
        for row in ledger.list_skills():
            typer.echo(f"{'on ' if row['enabled'] else 'off'}  {row['skill_id']}  v{row['version']}")
    finally:
        mem.close()


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
def run(
    port: int = typer.Option(8765, help="Local port (127.0.0.1 only)"),
    interval: float = typer.Option(None, help="Seconds between polls for every source; leave empty for the live defaults (Telegram instant, chats 5 s, Gmail 8 s)"),
    folder: list[str] = typer.Option([], "--folder", help="A local folder to watch (repeatable)"),  # noqa: B008
    demo: bool = typer.Option(False, "--demo", help="Demo mode: synthetic data for every app, every send is a local fake."),
) -> None:
    """Start the always-on agent without a window: continuous ingestion plus the local API."""
    from ragnarok.daemon.api import serve
    from ragnarok.daemon.launch import make_api
    from ragnarok.daemon.runtime import DEFAULT_INTERVALS
    from ragnarok.daemon.wiring import build_world

    if demo:
        from ragnarok.daemon.demo import build_demo_world

        world = build_demo_world()
    else:
        world = build_world(interval_s=interval or 60.0, files=list(folder) or None, intervals=None if interval else DEFAULT_INTERVALS, live_telegram_s=0 if interval else 25)
    typer.echo(f"ragnarok: running on 127.0.0.1:{port}; sources: {', '.join(world.runtime.runners) or 'none connected'}; session token file: {world.token_file}")
    serve(make_api(world, port), port)


@app.command()
def window(
    port: int = typer.Option(8765, help="Local port (127.0.0.1 only)"),
    interval: float = typer.Option(None, help="Seconds between polls for every source; leave empty for the live defaults (Telegram instant, chats 5 s, Gmail 8 s)"),
    folder: list[str] = typer.Option([], "--folder", help="A local folder to watch (repeatable)"),  # noqa: B008
    no_top: bool = typer.Option(False, "--no-top", help="Do not keep the window above other windows"),
    demo: bool = typer.Option(False, "--demo", help="Demo mode: synthetic data for every app, every send is a local fake. Never touches your real accounts or memory."),
) -> None:
    """The always-on window: a small always-on-top chat and activity panel with an on/off switch."""
    from ragnarok.daemon.launch import make_api, open_window, start_in_thread
    from ragnarok.daemon.runtime import DEFAULT_INTERVALS
    from ragnarok.daemon.wiring import build_world

    if demo:
        from ragnarok.daemon.demo import build_demo_world

        world = build_demo_world()
    else:
        world = build_world(interval_s=interval or 60.0, files=list(folder) or None, intervals=None if interval else DEFAULT_INTERVALS, live_telegram_s=0 if interval else 25)
    server, _thread = start_in_thread(make_api(world, port), port)
    try:
        open_window(port, world.guard.token, on_top=not no_top, services=world.services)
    finally:
        server.should_exit = True
        world.mem.close()


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


@app.command()
def evals(suite: list[str] = typer.Option(None, help="Run only these suites (default: all)"), report_only: bool = typer.Option(False, help="Only rebuild docs/EVALS.md")) -> None:
    """Run the benchmark suites against the real local models and write docs/EVALS.md."""
    import subprocess
    import sys

    cmd = [sys.executable, "-m", "evals.run"] + (["--suite", *suite] if suite else []) + (["--report-only"] if report_only else [])
    raise typer.Exit(subprocess.call(cmd))  # noqa: S603


def main() -> None:
    app()


if __name__ == "__main__":
    main()
