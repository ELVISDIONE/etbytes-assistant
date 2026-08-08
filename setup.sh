#!/usr/bin/env bash
# E.TBYTES Assistant — Setup
# Made by ELVISDIONE (E.TBYTES) · elvisteddy269@gmail.com
#
# Run this first on a fresh clone. It installs everything the assistant
# needs, then launches it straight into the first-run wizard.
set -e

echo "==================================="
echo "  E.TBYTES Assistant — Setup"
echo "==================================="
echo

if command -v pkg >/dev/null 2>&1; then
    echo "Termux detected — installing system packages..."
    pkg update -y
    pkg install -y python git termux-api mpv netcat-openbsd
    echo
fi

if command -v python3 >/dev/null 2>&1; then
    PYTHON=python3
elif command -v python >/dev/null 2>&1; then
    PYTHON=python
else
    echo "Error: Python 3.9+ is required but was not found on this system." >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Installing Python dependencies..."
"$PYTHON" -m pip install --upgrade pip
"$PYTHON" -m pip install -r "$SCRIPT_DIR/requirements.txt"

echo
echo "Setup complete! Launching E.TBYTES Assistant..."
echo
exec "$PYTHON" "$SCRIPT_DIR/etbytes_assistant.py"
