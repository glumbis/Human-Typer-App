#!/usr/bin/env bash
# macOS end-to-end test (CI): does the BUILT app really type into another app?
#   1. grants HumanTyper.app Accessibility / Input Monitoring / PostEvent in the TCC databases, the same
#      grants a user gives it in System Settings (GitHub's macOS runners have SIP disabled, so the
#      databases are writable with sudo),
#   2. opens an empty plain-text TextEdit document,
#   3. runs tools/mac_e2e_helper.py: launches the app like Finder does (`open`), presses F8 / fn+F8 /
#      fn+F9 as real HID key events and clicks the overlay / main-window buttons with real mouse events,
#      then reads what arrived in TextEdit.
# Results, screenshots, logs: ./e2e-out (CI artifact). Exit code 0 = all cases passed.
# Usage: bash tools/mac_e2e.sh dist/HumanTyper.app [open|direct] [cases]
set -u
APP="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
LAUNCH="${2:-open}"
CASES="${3:-setup_window,f8,fn_f8,overlay_start,overlay_pause_stop,hotkey_pause_stop,main_start,locate_clipboard,locate_count}"
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$(pwd)/e2e-out${E2E_SUFFIX:-}"
BUNDLE_ID="$(defaults read "$APP/Contents/Info" CFBundleIdentifier)"
rm -rf "$OUT"; mkdir -p "$OUT"

echo "== system"; sw_vers; uname -m; csrutil status

grant_tcc() {
  local req hex now csreq
  req="$(codesign -d -r- "$APP" 2>&1 | sed -n 's/^#* *designated => //p')"
  echo "designated requirement: $req"
  csreq="NULL"
  if [ -n "$req" ] && echo "$req" | csreq -r- -b "$OUT/req.bin" 2>/dev/null; then
    hex="$(xxd -p "$OUT/req.bin" | tr -d '\n')"; csreq="X'$hex'"
  fi
  now="$(date +%s)"
  for db in "/Library/Application Support/com.apple.TCC/TCC.db" "$HOME/Library/Application Support/com.apple.TCC/TCC.db"; do
    echo "== TCC $db"
    sudo sqlite3 "$db" ".schema access" | head -c 1200; echo
    for svc in kTCCServiceAccessibility kTCCServiceListenEvent kTCCServicePostEvent; do
      sudo sqlite3 "$db" "DELETE FROM access WHERE service='$svc' AND client='$BUNDLE_ID';
        INSERT INTO access (service, client, client_type, auth_value, auth_reason, auth_version, csreq, flags, last_modified)
        VALUES ('$svc', '$BUNDLE_ID', 0, 2, 3, 1, $csreq, 0, $now);" || echo "  insert $svc failed"
    done
    sudo sqlite3 "$db" "SELECT service, client, client_type, auth_value, auth_reason, length(csreq) FROM access
      WHERE service IN ('kTCCServiceAccessibility','kTCCServiceListenEvent','kTCCServicePostEvent','kTCCServiceAppleEvents','kTCCServiceScreenCapture');"
  done
  sudo pkill -9 tccd 2>/dev/null; sleep 2
}
# E2E_LEGACY=1 (diagnostics): sign like v1.4.3 did, i.e. ad-hoc with the implicit cdhash requirement
sign_app() {
  if [ "${E2E_LEGACY:-0}" = 1 ]; then codesign --force --sign - "$APP"
  else codesign --force --sign - --requirements "=designated => identifier \"$BUNDLE_ID\"" "$APP"; fi
}
if [ "${E2E_LEGACY:-0}" = 1 ] && [ "${E2E_RESIGN:-0}" != 1 ]; then
  echo "== legacy signing (v1.4.3 style)"; sign_app; codesign -d -r- "$APP" 2>&1 | grep designated
fi
[ "${E2E_GRANT:-1}" = 1 ] && grant_tcc

if [ "${E2E_RESIGN:-0}" = 1 ]; then
  # simulate an update: same app, new build (new cdhash), permissions NOT granted again
  echo "== simulated update: re-sign with changed contents (cdhash before/after)"
  codesign -dvvv "$APP" 2>&1 | grep -E "^CDHash=|designated"
  date > "$APP/Contents/Resources/e2e-update-marker.txt"
  sign_app
  codesign -dvvv "$APP" 2>&1 | grep -E "^CDHash="
  codesign -d -r- "$APP" 2>&1 | grep designated
fi

echo "== TextEdit (plain text, no autocorrect)"
defaults write com.apple.TextEdit RichText -int 0
for k in CorrectSpellingAutomatically CheckSpellingWhileTyping SmartQuotes SmartDashes TextReplacement SmartCopyPaste; do
  defaults write com.apple.TextEdit "$k" -bool false
done
for k in NSAutomaticSpellingCorrectionEnabled NSAutomaticCapitalizationEnabled NSAutomaticPeriodSubstitutionEnabled \
         NSAutomaticQuoteSubstitutionEnabled NSAutomaticDashSubstitutionEnabled NSAutomaticTextCompletionEnabled; do
  defaults write -g "$k" -bool false
done
pkill -x TextEdit 2>/dev/null; sleep 1
: > "$OUT/doc.txt"
open -a TextEdit "$OUT/doc.txt"; sleep 4
screencapture -x "$OUT/00-textedit-open.png" 2>/dev/null

pkill -f "$APP/Contents/MacOS/" 2>/dev/null; sleep 1
echo "== run ($LAUNCH launch): $CASES"
python3 "$HERE/mac_e2e_helper.py" --app "$APP" --dir "$OUT" --launch "$LAUNCH" --cases "$CASES"
RC=$?
pkill -f "$APP/Contents/MacOS/" 2>/dev/null
echo "== e2e.log"; cat "$OUT/e2e.log" 2>/dev/null | cut -c1-400
echo "== app output"; tail -n 40 "$OUT/app-output.log" 2>/dev/null
[ -s "$OUT/fault.log" ] && { echo "== faulthandler"; cat "$OUT/fault.log"; }
echo "== diag"; cat "$OUT/diag.json" 2>/dev/null | head -c 3000; echo
exit $RC
