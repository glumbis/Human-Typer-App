#!/usr/bin/env bash
# CI regression check for the macOS app: launch the built .app the way Finder does (LaunchServices
# `open`, so it runs as its own app WITHOUT Accessibility / Input Monitoring permission - the same
# situation as a user's first launch), drive the UI with the hidden --smoke flag, and fail if the
# app died or the smoke run did not finish. On failure, print the macOS crash report.
# Usage: bash tools/mac_launch_check.sh dist/HumanTyper.app [SECONDS]
set -u
APP="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
SECS="${2:-30}"
HERE="$(cd "$(dirname "$0")" && pwd)"
BIN="$APP/Contents/MacOS/$(defaults read "$APP/Contents/Info" CFBundleExecutable)"
WORK="${RUNNER_TEMP:-${TMPDIR:-/tmp}}/ht-launch"
rm -rf "$WORK"; mkdir -p "$WORK"
OUT="$WORK/output.log"; FAULT="$WORK/fault.log"; SMOKE="$WORK/smoke.log"
MARK="$WORK/.mark"; touch "$MARK"; sleep 1
pkill -f "$BIN" 2>/dev/null; sleep 1

echo "open $APP --smoke (must stay alive ${SECS}s)"
open -n --stdout "$OUT" --stderr "$OUT" --env HT_FAULT_LOG="$FAULT" "$APP" --args --smoke "$SMOKE"
ALIVE=1
for _ in $(seq 1 "$SECS"); do
  sleep 1
  pgrep -f "$BIN" >/dev/null || { ALIVE=0; break; }
done
pkill -f "$BIN" 2>/dev/null; sleep 2; pkill -9 -f "$BIN" 2>/dev/null

echo "---- smoke log ----"; cat "$SMOKE" 2>/dev/null
echo "---- app output ----"; tail -n 60 "$OUT" 2>/dev/null
[ -s "$FAULT" ] && { echo "---- faulthandler ----"; cat "$FAULT"; }
OK=1
[ "$ALIVE" = 1 ] || { echo "FAIL: the app died within ${SECS}s"; OK=0; }
grep -q "SMOKE DONE" "$SMOKE" 2>/dev/null || { echo "FAIL: smoke run did not finish"; OK=0; }
if [ "$OK" = 0 ]; then
  sleep 8                                   # give ReportCrash time to write the report
  REPORTS=$(find ~/Library/Logs/DiagnosticReports /Library/Logs/DiagnosticReports -newer "$MARK" \
            \( -name '*.ips' -o -name '*.crash' \) 2>/dev/null)
  if [ -n "$REPORTS" ]; then
    mkdir -p crash-reports && cp $REPORTS crash-reports/ 2>/dev/null
    python3 "$HERE/crash_summary.py" $REPORTS
  else
    echo "(no crash report found)"
  fi
  exit 1
fi
echo "OK: app stayed up ${SECS}s and finished the smoke run"
