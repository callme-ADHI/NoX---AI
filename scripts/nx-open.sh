#!/usr/bin/env bash
# nx-open.sh — wait for the NoX daemon to be ready, run the gate, open the UI
#
# This script is run by Hyprland exec-once after login.
# It waits up to 60 seconds for the daemon to respond, then:
#   1. Reads the session token
#   2. Runs the configured gate command (if mode=command)
#   3. Opens the UI via the launcher_cmd in config.toml

set -euo pipefail

NOX_DIR="${HOME}/nox"
TOKEN_FILE="${HOME}/.local/share/nox/session_token"
API_BASE="http://127.0.0.1:7777"

log() {
    echo "[nx-open] $*" >&2
}

# ── 1. Wait for the daemon ────────────────────────────────────────────────
log "Waiting for noxd to be ready..."
WAIT_SECS=60
for i in $(seq 1 $WAIT_SECS); do
    if curl -sf "${API_BASE}/api/status" > /dev/null 2>&1; then
        log "Daemon ready after ${i}s"
        break
    fi
    if [ "$i" -eq "$WAIT_SECS" ]; then
        log "ERROR: noxd did not become ready in ${WAIT_SECS}s"
        notify-send -u critical "NoX" "Daemon did not start in time. Run: nx doctor" 2>/dev/null || true
        exit 1
    fi
    sleep 1
done

# ── 2. Read session token ────────────────────────────────────────────────
if [ ! -f "${TOKEN_FILE}" ]; then
    log "ERROR: session token not found at ${TOKEN_FILE}"
    exit 1
fi
TOKEN=$(cat "${TOKEN_FILE}")

# ── 3. Run gate if needed ────────────────────────────────────────────────
GATE_MODE=$(curl -sf "${API_BASE}/api/status" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('gate_mode','token_only'))" 2>/dev/null || echo "token_only")

if [ "${GATE_MODE}" = "command" ]; then
    GATE_CMD=$(python3 -c "
import tomllib, pathlib, json
cfg_file = pathlib.Path('${HOME}/.config/nox/config.toml')
if cfg_file.exists():
    cfg = tomllib.loads(cfg_file.read_text())
    print(json.dumps(cfg.get('gate', {}).get('command', [])))
else:
    print('[]')
")
    if [ "${GATE_CMD}" != "[]" ]; then
        log "Running gate command..."
        # Parse and run the command from the JSON array
        if ! python3 -c "import json, subprocess, sys; cmd=json.loads(sys.argv[1]); sys.exit(subprocess.run(cmd).returncode)" "${GATE_CMD}"; then
            log "Gate authentication failed"
            notify-send -u critical "NoX" "Authentication failed. UI not opened." 2>/dev/null || true
            exit 1
        fi
        log "Gate passed"
    fi
fi

# ── 4. Open the UI ───────────────────────────────────────────────────────
UI_URL="${API_BASE}/?t=${TOKEN}"
log "Opening UI: ${UI_URL}"

# Read launcher_cmd from config
LAUNCHER=$(python3 -c "
import tomllib, pathlib, json
cfg_file = pathlib.Path('${HOME}/.config/nox/config.toml')
if cfg_file.exists():
    cfg = tomllib.loads(cfg_file.read_text())
    cmd = cfg.get('ui', {}).get('launcher_cmd', ['chromium', '--app={url}'])
    # substitute {url}
    cmd = [c.replace('{url}', '${UI_URL}') for c in cmd]
    print(json.dumps(cmd))
else:
    print(json.dumps(['chromium', '--app=${UI_URL}']))
")

python3 -c "
import json, subprocess, sys
cmd = json.loads(sys.argv[1])
subprocess.Popen(cmd)
" "${LAUNCHER}" &

log "Done."
