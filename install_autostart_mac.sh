#!/bin/bash
# Runs SwingSignals Desk at login on macOS (launchd). Run start_mac_linux.sh once first.
DIR="$(cd "$(dirname "$0")" && pwd)"; P="$HOME/Library/LaunchAgents/com.swingsignals.desk.plist"
cat > "$P" <<PL
<?xml version="1.0" encoding="UTF-8"?><!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict><key>Label</key><string>com.swingsignals.desk</string>
<key>ProgramArguments</key><array><string>$DIR/.venv/bin/python</string><string>$DIR/app/server.py</string><string>--no-browser</string></array>
<key>WorkingDirectory</key><string>$DIR</string><key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
<key>StandardOutPath</key><string>$DIR/data/launchd.log</string><key>StandardErrorPath</key><string>$DIR/data/launchd.log</string></dict></plist>
PL
launchctl unload "$P" 2>/dev/null; launchctl load "$P" && echo "Installed. Open http://127.0.0.1:8765"
