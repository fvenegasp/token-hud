#!/bin/bash
# Construye Token HUD (release) y empaqueta "dist/Token HUD.app" listo para firmar.
# Uso: ./build-app.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

APP="dist/Token HUD.app"

swift build -c release
BIN_DIR="$(swift build -c release --show-bin-path)"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN_DIR/CuotaBar" "$APP/Contents/MacOS/TokenHUD"

# SwiftPM genera un bundle de recursos propio para el target ejecutable
# (CuotaBar_CuotaBar.bundle, con Resources/logos/*.svg). El `Bundle.module`
# generado lo busca en el resourceURL del bundle principal, así que basta
# copiarlo tal cual a Contents/Resources.
RESOURCE_BUNDLE="$(find "$BIN_DIR" -maxdepth 1 -name '*.bundle' -print -quit)"
if [[ -z "$RESOURCE_BUNDLE" ]]; then
  echo "error: no se encontró el bundle de recursos en $BIN_DIR" >&2
  exit 1
fi
cp -R "$RESOURCE_BUNDLE" "$APP/Contents/Resources/"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>CFBundleIdentifier</key>
	<string>com.fan.tokenhud</string>
	<key>CFBundleName</key>
	<string>Token HUD</string>
	<key>CFBundleDisplayName</key>
	<string>Token HUD</string>
	<key>CFBundleExecutable</key>
	<string>TokenHUD</string>
	<key>CFBundlePackageType</key>
	<string>APPL</string>
	<key>CFBundleShortVersionString</key>
	<string>1.0</string>
	<key>CFBundleVersion</key>
	<string>1</string>
	<key>LSUIElement</key>
	<true/>
	<key>LSMinimumSystemVersion</key>
	<string>14.0</string>
</dict>
</plist>
PLIST

plutil -lint "$APP/Contents/Info.plist"

codesign --force --sign - "$APP"

echo "OK: $APP"
