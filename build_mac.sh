#!/usr/bin/env bash
# macOS build: dist/HumanTyper.app + dist/HumanTyper-<version>-mac-<arch>.dmg (arch of the machine running this).
# Usage: bash build_mac.sh   (CI: .github/workflows/macos.yml)
set -euo pipefail
cd "$(dirname "$0")"

python3 -m pip install -r requirements.txt pyinstaller
python3 tools/fetch_engine.py
if [ ! -f assets/icon.icns ] || [ ! -f assets/icon.png ]; then python3 tools/make_icon.py; fi

VERSION="$(python3 -c 'import humantyper; print(humantyper.__version__)')"
case "$(uname -m)" in arm64) ARCH=arm64 ;; *) ARCH=x86_64 ;; esac

rm -rf dist/HumanTyper.app dist/HumanTyper build/HumanTyper
python3 -m PyInstaller packaging/HumanTyper.spec --noconfirm --clean
APP=dist/HumanTyper.app
[ -d "$APP" ] || { echo "PyInstaller did not produce $APP"; exit 1; }
chmod +x "$APP"/Contents/Frameworks/llama/llama-server 2>/dev/null || true

# Ad-hoc signature: required for the app to launch on Apple Silicon. Not a Developer ID signature,
# so Gatekeeper still asks on first launch (see README).
codesign --force --deep --sign - "$APP"
# Re-sign the outer bundle with an explicit designated requirement "identifier <bundle id>". An ad-hoc
# signature's implicit requirement is its cdhash, which changes with every build, so macOS silently
# stopped honouring the Accessibility / Input Monitoring switches after each update (still shown ON
# in System Settings). With this requirement the grants keep working for new versions.
BUNDLE_ID="$(defaults read "$PWD/$APP/Contents/Info" CFBundleIdentifier)"
codesign --force --sign - --requirements "=designated => identifier \"$BUNDLE_ID\"" "$APP"
codesign -d -r- "$APP" 2>&1 | grep designated
codesign --verify --deep --verbose=2 "$APP" || true

# Selftest inside the bundle (no GUI, no typing): exit 0 = PASS
REPORT="${TMPDIR:-/tmp}/ht_selftest.txt"
rm -f "$REPORT"
set +e
"$APP/Contents/MacOS/HumanTyper" --selftest "$REPORT"
RC=$?
set -e
[ -f "$REPORT" ] && cat "$REPORT"
if [ "$RC" -ne 0 ]; then echo "selftest FAILED (exit $RC)"; exit "$RC"; fi

# DMG: app + Applications symlink
STAGE="$(mktemp -d)"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
DMG="dist/HumanTyper-$VERSION-mac-$ARCH.dmg"
rm -f "$DMG"
hdiutil create -volname "Human Typer" -srcfolder "$STAGE" -ov -format UDZO "$DMG"
rm -rf "$STAGE"
echo "Built: $DMG"
ls -lh "$DMG"
