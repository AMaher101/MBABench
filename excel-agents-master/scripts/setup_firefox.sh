#!/bin/bash
# Firefox Browser Session Launcher
# The Python script will automatically install Playwright browsers if needed
# Uses uv run to ensure correct virtual environment

cd "$(dirname "$0")/.."

chmod +x excel_agent/firefox_browser.py

uv run python excel_agent/firefox_browser.py "$@"
