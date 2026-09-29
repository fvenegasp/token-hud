#!/bin/bash
# LaunchAgent entry point: load PATH from the env file, then run one collection pass.
# Exit code 75 (another collector running) is passed through; launchd just logs it.
set -euo pipefail

ENV_FILE="$HOME/.config/cuota/env"
if [[ ! -r "$ENV_FILE" ]]; then
    echo "cuota run.sh: missing $ENV_FILE (run collector/launchd/install.sh install)" >&2
    exit 78
fi

set -a
# shellcheck source=/dev/null
source "$ENV_FILE"
set +a
export PATH

exec "$HOME/.local/bin/cuota" collect
