#!/bin/bash
# Instala/desinstala Token HUD en ~/Applications con su LaunchAgent.
# Uso: ./install.sh install|uninstall|status [--dry-run]
set -euo pipefail

LABEL="com.fan.tokenhud"
LEGACY_LABEL="com.fan.cuotabar"
ROOT="$(cd "$(dirname "$0")" && pwd)"
SRC_APP="$ROOT/dist/Token HUD.app"
DST_DIR="$HOME/Applications"
DST_APP="$DST_DIR/Token HUD.app"
LEGACY_APP="$DST_DIR/CuotaBar.app"
AGENT_DIR="$HOME/Library/LaunchAgents"
PLIST="$AGENT_DIR/$LABEL.plist"
LEGACY_PLIST="$AGENT_DIR/$LEGACY_LABEL.plist"
GUI="gui/$(id -u)"

COMMAND="${1:-}"
DRY_RUN=0
for arg in "$@"; do
  if [[ "$arg" == "--dry-run" ]]; then DRY_RUN=1; fi
done

run() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf '+ %s\n' "$*"
  else
    "$@"
  fi
}

write_plist() {
  local content
  content="<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">
<plist version=\"1.0\">
<dict>
	<key>Label</key>
	<string>$LABEL</string>
	<key>ProgramArguments</key>
	<array>
		<string>$DST_APP/Contents/MacOS/TokenHUD</string>
	</array>
	<key>RunAtLoad</key>
	<true/>
	<key>KeepAlive</key>
	<dict>
		<key>SuccessfulExit</key>
		<false/>
	</dict>
	<key>ProcessType</key>
	<string>Interactive</string>
</dict>
</plist>"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf '+ escribir %s:\n%s\n' "$PLIST" "$content"
  else
    printf '%s\n' "$content" > "$PLIST"
  fi
}

# bootout devuelve antes de que launchd termine de bajar el servicio.
wait_unloaded() { # wait_unloaded <domain/label>
  local target="$1" i
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf '+ wait until unloaded %s\n' "$target"
    return 0
  fi
  for ((i = 0; i < 50; i++)); do
    launchctl print "$target" >/dev/null 2>&1 || return 0
    sleep 0.2
  done
  echo "error: $target sigue cargado tras 10 s del bootout" >&2
  exit 1
}

bootout_and_wait() { # bootout_and_wait <domain/label>
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf '+ launchctl bootout %s (ignorar error)\n' "$1"
  else
    launchctl bootout "$1" 2>/dev/null || true
  fi
  wait_unloaded "$1"
}

bootstrap_retry() { # bootstrap_retry <domain> <plist>
  local n
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf '+ launchctl bootstrap %s %s (hasta 3 intentos, 1 s entre ellos)\n' "$1" "$2"
    return 0
  fi
  for n in 1 2 3; do
    launchctl bootstrap "$1" "$2" && return 0
    if [[ "$n" -lt 3 ]]; then sleep 1; fi
  done
  echo "error: launchctl bootstrap fallo tras 3 intentos" >&2
  exit 1
}

remove_legacy() {
  bootout_and_wait "$GUI/$LEGACY_LABEL"
  run rm -f "$LEGACY_PLIST"
  run rm -rf "$LEGACY_APP"
}

cmd_install() {
  if [[ ! -d "$SRC_APP" ]]; then
    echo "error: falta $SRC_APP (ejecuta ./build-app.sh primero)" >&2
    exit 1
  fi
  remove_legacy
  run mkdir -p "$DST_DIR" "$AGENT_DIR"
  run rm -rf "$DST_APP"
  run cp -R "$SRC_APP" "$DST_APP"
  write_plist
  run plutil -lint "$PLIST"
  # Idempotente: bootout ignora el error si el agente no estaba cargado.
  bootout_and_wait "$GUI/$LABEL"
  bootstrap_retry "$GUI" "$PLIST"
  echo "Instalado: $DST_APP (LaunchAgent $LABEL)"
}

cmd_uninstall() {
  bootout_and_wait "$GUI/$LABEL"
  run rm -f "$PLIST"
  run rm -rf "$DST_APP"
  remove_legacy
  echo "Desinstalado."
}

cmd_status() {
  if [[ -d "$DST_APP" ]]; then
    echo "app: $DST_APP (instalada)"
  else
    echo "app: no instalada"
  fi
  if [[ -f "$PLIST" ]]; then
    echo "plist: $PLIST (presente)"
  else
    echo "plist: no presente"
  fi
  if launchctl print "$GUI/$LABEL" >/dev/null 2>&1; then
    echo "launchd: cargado ($GUI/$LABEL)"
  else
    echo "launchd: no cargado"
  fi
}

case "$COMMAND" in
  install) cmd_install ;;
  uninstall) cmd_uninstall ;;
  status) cmd_status ;;
  *)
    echo "uso: $0 install|uninstall|status [--dry-run]" >&2
    exit 2
    ;;
esac
