#!/usr/bin/env bash
set -e
echo "Installing Playwright..."
pip install playwright
echo "Installing Chromium browser..."
playwright install chromium
echo "Installing system dependencies..."
playwright install-deps chromium
echo "Done! Browser subsystem is ready."
