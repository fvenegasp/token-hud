#!/bin/bash
# Save the JSON that Claude Code pipes to a statusLine command, then pass it through unchanged.
#
# The cuota Claude reader takes its 5 h / weekly quota from the `rate_limits` field of that JSON.
# Claude Code only runs the statusLine command while a session is active, so the data goes stale otherwise.
#
# Usage in ~/.claude/settings.json (chain it in front of your own status line command, or use it alone):
#   "statusLine": {
#     "type": "command",
#     "command": "/path/to/token-hud/collector/extras/claude-statusline-tee.sh | your-statusline-command"
#   }
# Alone (Claude shows the raw JSON as the status line text; prefer chaining with a real renderer):
#   "command": "/path/to/token-hud/collector/extras/claude-statusline-tee.sh"
#
# Destination: $CUOTA_CLAUDE_STATUSLINE_FILE, default ~/.cache/cuota/claude-statusline.json.
# Written atomically (temp file + rename), mode 0600. Failures to write never break the pipeline.
set -u

DEST="${CUOTA_CLAUDE_STATUSLINE_FILE:-$HOME/.cache/cuota/claude-statusline.json}"
DIR="$(dirname "$DEST")"
TMP=""

if (umask 077 && mkdir -p "$DIR") 2>/dev/null; then
    TMP="$(umask 077 && mktemp "$DIR/.claude-statusline.XXXXXX" 2>/dev/null)" || TMP=""
fi

if [[ -n "$TMP" ]]; then
    # tee passes stdin through to stdout while writing the copy
    tee "$TMP"
    if [[ -s "$TMP" ]]; then
        chmod 600 "$TMP" 2>/dev/null
        mv -f "$TMP" "$DEST" 2>/dev/null || rm -f "$TMP"
    else
        rm -f "$TMP"
    fi
else
    cat
fi
exit 0
