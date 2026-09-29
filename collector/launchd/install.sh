#!/bin/bash
# Install / uninstall / inspect the com.fan.cuota LaunchAgent (lives in collector/launchd/). Idempotent. Never writes credentials.
# Usage: install.sh [--dry-run] {install|uninstall [--purge] [--yes]|status|render <outpath>}
set -euo pipefail

LABEL="com.fan.cuota"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HERE="$REPO/launchd"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
ENV_DIR="$HOME/.config/cuota"
ENV_FILE="$ENV_DIR/env"
DATA_DIR="$HOME/.cache/cuota"
LOG_FILE="$DATA_DIR/launchd.log"
DOMAIN="gui/$(id -u)"
DRY=0

run() {
    if ((DRY)); then
        printf '[dry-run] %s\n' "$*"
    else
        "$@"
    fi
}

is_loaded() { launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; }

# bootout returns before launchd finishes tearing the service down.
wait_unloaded() { # wait_unloaded <domain/label>
    local target="$1" i
    if ((DRY)); then
        echo "[dry-run] + wait until unloaded $target"
        return 0
    fi
    for ((i = 0; i < 50; i++)); do
        launchctl print "$target" >/dev/null 2>&1 || return 0
        sleep 0.2
    done
    echo "error: $target still loaded 10 s after bootout" >&2
    exit 1
}

bootstrap_retry() { # bootstrap_retry <domain> <plist>
    local n
    if ((DRY)); then
        echo "[dry-run] launchctl bootstrap $1 $2 (up to 3 attempts, 1 s apart)"
        return 0
    fi
    for n in 1 2 3; do
        launchctl bootstrap "$1" "$2" && return 0
        if ((n < 3)); then sleep 1; fi
    done
    echo "error: launchctl bootstrap failed after 3 attempts" >&2
    exit 1
}

candidates() { # candidate absolute paths for a binary, in priority order, deduped
    local bin="$1" seen=":" c
    {
        /bin/zsh -lc "which -a $bin" 2>/dev/null || true
        /bin/zsh -ic "which -a $bin" 2>/dev/null || true
        # shellcheck disable=SC2012
        ls -1d "$HOME"/.nvm/versions/node/*/bin/"$bin" 2>/dev/null | sort -rV || true
    } | while IFS= read -r c; do
        [[ "$c" == /* && -x "$c" ]] || continue
        printf '%s\n' "$c"
    done | while IFS= read -r c; do
        [[ "$seen" == *":$c:"* ]] && continue
        seen+="$c:"
        printf '%s\n' "$c"
    done
}

# pick_bin <bin>: prints "<path>\t<version line>" for the first candidate whose --version exits 0 in 10s
pick_bin() {
    local bin="$1" c out rc report="" found=0
    while IFS= read -r c; do
        found=1
        rc=0
        out="$(cd /tmp && perl -e 'alarm 10; exec @ARGV' "$c" --version 2>&1 </dev/null)" || rc=$?
        if ((rc == 0)); then
            printf '%s\t%s\n' "$c" "$(printf '%s' "$out" | head -n 1)"
            return 0
        fi
        report+="  $c: exit $rc: $(printf '%s' "$out" | head -n 1)"$'\n'
    done < <(candidates "$bin")
    echo "error: no working '$bin' found" >&2
    if ((found)); then printf '%s' "$report" >&2; else echo "  (no candidates)" >&2; fi
    return 1
}

render() { # render <outpath>: template -> plist with absolute paths
    local out="$1" tpl="$HERE/com.fan.cuota.plist.template"
    sed -e "s|@RUN_SH@|$HERE/run.sh|g" -e "s|@LOG_FILE@|$LOG_FILE|g" -e "s|@HOME@|$HOME|g" "$tpl" >"$out"
    plutil -lint "$out"
}

build_path() { # prints the PATH value; chosen binaries are reported on stderr
    local dirs=() d out="" b picked p v
    for b in codex agy; do
        picked="$(pick_bin "$b")" || return 1
        p="${picked%%$'\t'*}"
        v="${picked#*$'\t'}"
        echo "$b: $p ($v)" >&2
        dirs+=("$(dirname "$p")")
    done
    dirs+=("$HOME/.local/bin" /opt/homebrew/bin /usr/bin /bin /usr/sbin /sbin)
    local seen=":"
    for d in "${dirs[@]}"; do
        [[ "$seen" == *":$d:"* ]] && continue
        seen+="$d:"
        out+="${out:+:}$d"
    done
    printf '%s' "$out"
}

cmd_install() {
    local path
    run uv tool install --editable "$REPO" --force
    path="$(build_path)" || exit 1
    if ((DRY)); then
        echo "[dry-run] write $ENV_FILE (0600): PATH=$path"
    else
        mkdir -p "$ENV_DIR"
        (umask 077 && printf 'PATH=%s\n' "$path" >"$ENV_FILE")
        chmod 600 "$ENV_FILE"
    fi
    run mkdir -p "$DATA_DIR" "$(dirname "$PLIST")"
    if ((DRY)); then
        echo "[dry-run] render template -> $PLIST and plutil -lint"
    else
        render "$PLIST"
    fi
    if is_loaded; then
        run launchctl bootout "$DOMAIN/$LABEL"
        wait_unloaded "$DOMAIN/$LABEL"
    fi
    bootstrap_retry "$DOMAIN" "$PLIST"
    if ((DRY)); then return 0; fi
    cmd_status
}

cmd_uninstall() {
    local purge=0 yes=0 a ans
    for a in "$@"; do
        case "$a" in
            --purge) purge=1 ;;
            --yes) yes=1 ;;
            *) echo "unknown option: $a" >&2; exit 2 ;;
        esac
    done
    if is_loaded; then
        run launchctl bootout "$DOMAIN/$LABEL"
        wait_unloaded "$DOMAIN/$LABEL"
    else
        echo "not loaded"
    fi
    run rm -f "$PLIST" "$ENV_FILE"
    if ((purge)); then
        ans=n
        if ((yes)); then
            ans=y
        elif ((DRY)); then
            echo "[dry-run] would ask: remove $DATA_DIR? [y/N]"
        else
            read -r -p "Remove $DATA_DIR (state, history, logs)? [y/N] " ans
        fi
        run uv tool uninstall cuota
        if [[ "$ans" == [yY]* ]]; then run rm -rf "$DATA_DIR"; else echo "kept $DATA_DIR"; fi
    else
        echo "kept $DATA_DIR (data) and the uv tool; use --purge to remove them"
    fi
}

cmd_status() {
    local info state age
    if info="$(launchctl print "$DOMAIN/$LABEL" 2>/dev/null)"; then
        echo "loaded:    yes"
        echo "$info" | awk -F' = ' '/^\t(state|runs|last exit code) = /{gsub(/^\t/,"");print "           " $0}'
    else
        echo "loaded:    no (not loaded)"
    fi
    echo "plist:     $PLIST $([[ -f "$PLIST" ]] && echo present || echo missing)"
    echo "env file:  $ENV_FILE $([[ -f "$ENV_FILE" ]] && echo present || echo missing)"
    local b w v envpath
    envpath="$(sed -n 's/^PATH=//p' "$ENV_FILE" 2>/dev/null || true)"
    for b in codex agy; do
        w="$(PATH="${envpath:-$PATH}" command -v "$b" || true)"
        if [[ -n "$w" ]]; then
            v="$(perl -e 'alarm 10; exec @ARGV' "$w" --version 2>&1 </dev/null | head -n 1 || true)"
            echo "binary:    $b -> $w (${v:-no version output})"
        else
            echo "binary:    $b not found"
        fi
    done
    state="$DATA_DIR/state.json"
    if [[ -f "$state" ]]; then
        age=$(($(date +%s) - $(stat -f %m "$state")))
        echo "state.json: updated ${age}s ago"
    else
        echo "state.json: missing"
    fi
    echo "collect.log (last 5):"
    if [[ -f "$DATA_DIR/collect.log" ]]; then tail -n 5 "$DATA_DIR/collect.log" | sed 's/^/  /'; else echo "  (none)"; fi
}

args=()
for a in "$@"; do
    if [[ "$a" == "--dry-run" ]]; then DRY=1; else args+=("$a"); fi
done
set -- ${args[@]+"${args[@]}"}

case "${1:-}" in
    install) cmd_install ;;
    uninstall) shift; cmd_uninstall "$@" ;;
    status) cmd_status ;;
    render) [[ -n "${2:-}" ]] || { echo "usage: render <outpath>" >&2; exit 2; }; render "$2" ;;
    *) echo "usage: $0 [--dry-run] {install|uninstall [--purge] [--yes]|status|render <outpath>}" >&2; exit 2 ;;
esac
