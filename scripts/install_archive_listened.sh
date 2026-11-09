#!/usr/bin/env bash
# Install (or reinstall) the Mac launchd job that auto-archives finished episodes.
# Every 30 min it reads Apple Podcasts' play state, archives Listen Queue cards you've
# finished (>= 90% or marked played), and republishes the feed. See CLAUDE.md
# "Auto-archive finished episodes".
#
#   scripts/install_archive_listened.sh            # install + start
#   scripts/install_archive_listened.sh uninstall  # stop + remove
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
LABEL="com.jeqcho.counterfactual-podcast.archive-listened"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
if [[ "${1:-}" == "uninstall" ]]; then
  rm -f "$PLIST"
  echo "removed $LABEL"
  exit 0
fi

uv sync --quiet
PY="$ROOT/.venv/bin/python"
REAL_PY="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$PY")"
mkdir -p "$ROOT/logs" "$HOME/Library/LaunchAgents"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string>
    <string>-m</string>
    <string>counterfactual_podcast.pipelines.archive_listened</string>
    <string>--apply</string>
  </array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>StartInterval</key><integer>1800</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$ROOT/logs/archive-listened.launchd.log</string>
  <key>StandardErrorPath</key><string>$ROOT/logs/archive-listened.launchd.log</string>
</dict>
</plist>
EOF

launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "installed $LABEL (runs every 30 min; log: logs/archive-listened.launchd.log)"
echo
echo "If the log shows 'Operation not permitted' / 'authorization denied', give this"
echo "binary Full Disk Access (System Settings > Privacy & Security > Full Disk Access > +):"
echo "  $REAL_PY"
echo "then rerun this script."
