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
