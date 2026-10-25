#!/bin/sh
# macOS only: clear the hidden flag on the editable-install .pth file (see docs/adr/002-macos-hidden-pth.md).
chflags -R nohidden .venv 2>/dev/null && echo "fixed"
