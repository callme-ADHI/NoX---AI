#!/usr/bin/env bash
# install.sh — idempotent NoX installation script
#
# Run from the cloned nox/ directory:
#   bash scripts/install.sh
#
# What this does:
#   1. Creates the Python venv with uv
#   2. Installs the noxai package
#   3. Builds the UI (if Node/npm is available)
#   4. Creates config/data/state directories with correct permissions
#   5. Copies config.example.toml to ~/.config/nox/ if not present
#   6. Installs and enables the systemd user service
#   7. Makes scripts executable
#   8. Runs nx doctor

set -euo pipefail

NOX_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${NOX_DIR}/.venv"

log() { echo "[install] $*"; }
warn() { echo "[install] WARNING: $*" >&2; }

# ── 1. Ensure uv is available ────────────────────────────────────────────
if ! command -v uv &> /dev/null; then
    if [ -x "${HOME}/.local/bin/uv" ]; then
        export PATH="${HOME}/.local/bin:${PATH}"
    else
        log "Installing uv..."
        curl -LsSf https://astral.sh/uv/install.sh | sh
        export PATH="${HOME}/.local/bin:${PATH}"
    fi
fi
log "uv: $(uv --version)"

# ── 2. Create venv ───────────────────────────────────────────────────────
log "Creating venv at ${VENV}..."
uv venv "${VENV}" --python 3.14 2>/dev/null || uv venv "${VENV}" --python python3

# ── 3. Install package ───────────────────────────────────────────────────
log "Installing noxai package..."
uv pip install --python "${VENV}/bin/python" -e "${NOX_DIR}"

# ── 4. Build UI (optional) ───────────────────────────────────────────────
UI_DIR="${NOX_DIR}/ui"
if [ -d "${UI_DIR}" ] && command -v npm &> /dev/null; then
    log "Building UI..."
    (cd "${UI_DIR}" && npm install --silent && npm run build --silent)
else
    warn "npm not found or ui/ directory missing. Skipping UI build."
    warn "Build manually: cd ui && npm install && npm run build"
fi

# ── 5. Create directories ────────────────────────────────────────────────
log "Creating config/data/state directories..."
mkdir -p \
    "${HOME}/.config/nox" \
    "${HOME}/.local/share/nox/skills" \
    "${HOME}/.local/share/nox/tools" \
    "${HOME}/.local/state/nox/logs"

# Config file (copy example if not present)
if [ ! -f "${HOME}/.config/nox/config.toml" ]; then
    cp "${NOX_DIR}/config.example.toml" "${HOME}/.config/nox/config.toml"
    log "Created ~/.config/nox/config.toml (from example)"
fi

# ── 6. Install systemd user service ─────────────────────────────────────
SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"
mkdir -p "${SYSTEMD_USER_DIR}"

SERVICE_SRC="${NOX_DIR}/systemd/noxd.service"
SERVICE_DST="${SYSTEMD_USER_DIR}/noxd.service"

if [ ! -f "${SERVICE_DST}" ] || ! diff -q "${SERVICE_SRC}" "${SERVICE_DST}" &> /dev/null; then
    cp "${SERVICE_SRC}" "${SERVICE_DST}"
    log "Installed noxd.service"
fi

systemctl --user daemon-reload
systemctl --user enable noxd.service
log "noxd.service enabled"

# ── 7. Make scripts executable ───────────────────────────────────────────
chmod +x "${NOX_DIR}/scripts/nx-open.sh"
chmod +x "${NOX_DIR}/scripts/install.sh"

# ── 8. Print Hyprland instruction ────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Add this line to ~/.config/hypr/hyprland.conf:"
echo ""
echo "    exec-once = ~/nox/scripts/nx-open.sh"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Fish shell instructions
echo "  Fish shell activation:"
echo "    source ${VENV}/bin/activate.fish"
echo ""
echo "  Or add the nx command directly:"
echo "    fish_add_path ${VENV}/bin"
echo ""

# ── 9. Run nx doctor ─────────────────────────────────────────────────────
log "Running nx doctor..."
"${VENV}/bin/nx" doctor || true  # don't fail the install on doctor warnings

log "Installation complete."
