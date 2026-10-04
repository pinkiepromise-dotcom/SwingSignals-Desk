#!/bin/bash
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  echo "First run: setting up Python environment..."
  python3 -m venv .venv && .venv/bin/pip install --upgrade pip
fi
# Re-install packages whenever requirements.txt changed (new versions add packages)
if ! cmp -s requirements.txt .venv/installed_requirements.txt; then
  echo "Installing / updating packages..."
  .venv/bin/pip install -r requirements.txt && cp requirements.txt .venv/installed_requirements.txt
fi
exec .venv/bin/python app/server.py "$@"
