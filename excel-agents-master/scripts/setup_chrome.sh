#!/bin/bash
# Chrome Browser Session Launcher
# This uses the real Chrome Canary browser via CDP to bypass Cloudflare
# Works on macOS, Windows (Git Bash), and Linux

cd "$(dirname "$0")/.."

echo "Chrome Browser Session"
echo "======================"
echo ""
echo "This uses your REAL Chrome Canary browser to bypass Cloudflare detection."
echo "Your session is saved to: ~/.chrome-canary-automation"
echo ""

# Run the Python script (it handles cross-platform Chrome detection)
uv run python excel_agent/chrome_browser.py "$@"
