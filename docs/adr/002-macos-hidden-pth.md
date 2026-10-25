# ADR 002: macOS hides the editable-install .pth file

**Context.** On macOS the editable install file `.venv/lib/python3.12/site-packages/_editable_impl_ragnarok.pth` is created with the `hidden` file flag, and Python 3.12 skips hidden `.pth` files. `import ragnarok` then fails outside pytest, including for the `ragnarok` console script and for subprocesses.

**Choice.** (1) pytest sets `pythonpath = ["src"]`; (2) every subprocess we launch passes an explicit `PYTHONPATH` to `src`; (3) on a Mac, `scripts/mac_fix_pth.sh` runs `chflags -R nohidden .venv` after any `uv sync`. Windows and Linux are not affected.

**Why.** Code must not depend on a flag a build tool sets. **Reverse.** Drop the editable install and run with `PYTHONPATH=src`.
