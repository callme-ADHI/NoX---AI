#!/usr/bin/env bash
# scripts/verify-install.sh — verify NoX Arch Linux packaging and installation
set -uo pipefail

BOLD="\033[1m"
GREEN="\033[32m"
YELLOW="\033[33m"
RED="\033[31m"
BLUE="\033[34m"
RESET="\033[0m"

pass() { echo -e "  [${GREEN}PASS${RESET}] $*"; }
warn() { echo -e "  [${YELLOW}WARN${RESET}] $*"; }
fail() { echo -e "  [${RED}FAIL${RESET}] $*"; ((ERRORS++)); }
info() { echo -e "${BOLD}${BLUE}==>${RESET} ${BOLD}$*${RESET}"; }

ERRORS=0

echo -e "\n${BOLD}====================================================${RESET}"
echo -e "${BOLD}       NoX Arch Linux Installation Verification     ${RESET}"
echo -e "${BOLD}====================================================${RESET}\n"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PKG_DIR="${REPO_ROOT}/packaging/arch"

# 1. Package Archive Verification
info "1. Checking Arch Package Archives (in packaging/arch)..."
NOX_PKG=$(ls -1 "${PKG_DIR}"/nox-[0-9]*.pkg.tar.zst 2>/dev/null | grep -v 'nox-app' | head -n 1 || true)
NOX_APP_PKG=$(ls -1 "${PKG_DIR}"/nox-app-[0-9]*.pkg.tar.zst 2>/dev/null | head -n 1 || true)

if [ -n "$NOX_PKG" ] && [ -f "$NOX_PKG" ]; then
    pass "Found nox package: $(basename "$NOX_PKG")"
    ARCHIVE_FILES=$(bsdtar -tf "$NOX_PKG")
    
    for f in "usr/bin/nx" "usr/bin/noxd" "usr/lib/systemd/user/noxd.service" "usr/share/icons/hicolor/scalable/apps/nox.svg" "opt/nox/venv/bin/nx" "opt/nox/ui/dist/index.html"; do
        if bsdtar -tf "$NOX_PKG" | grep -Fq "$f"; then
            pass "nox package contains $f"
        else
            fail "nox package missing required path: $f"
        fi
    done
else
    fail "No nox-*.pkg.tar.zst found in ${PKG_DIR} (run 'make package')"
fi

if [ -n "$NOX_APP_PKG" ] && [ -f "$NOX_APP_PKG" ]; then
    pass "Found nox-app package: $(basename "$NOX_APP_PKG")"
    APP_FILES=$(bsdtar -tf "$NOX_APP_PKG")
    for f in "usr/bin/nox-app" "usr/share/applications/nox.desktop" "opt/nox/bin/nox-desktop"; do
        if bsdtar -tf "$NOX_APP_PKG" | grep -Fq "$f"; then
            pass "nox-app package contains $f"
        else
            fail "nox-app package missing required path: $f"
        fi
    done
else
    fail "No nox-app-*.pkg.tar.zst found in ${PKG_DIR} (run 'make package')"
fi

# 2. System Service Unit Validation
info "2. Validating systemd user service unit..."
SERVICE_FILE="${PKG_DIR}/noxd.service"
if [ -f "$SERVICE_FILE" ]; then
    pass "Found unit template at ${SERVICE_FILE}"
    # Verify unit syntax
    if systemd-analyze verify "$SERVICE_FILE" 2>&1 | grep -q 'error'; then
        fail "systemd-analyze verify reported errors on $SERVICE_FILE"
    else
        pass "systemd-analyze verify passed for noxd.service"
    fi
else
    fail "noxd.service not found at ${SERVICE_FILE}"
fi

# 3. Desktop Entry Validation
info "3. Validating desktop entry..."
DESKTOP_FILE="${PKG_DIR}/nox.desktop"
if [ -f "$DESKTOP_FILE" ]; then
    pass "Found desktop file at ${DESKTOP_FILE}"
    if desktop-file-validate "$DESKTOP_FILE"; then
        pass "desktop-file-validate passed with 0 errors"
    else
        fail "desktop-file-validate failed for ${DESKTOP_FILE}"
    fi
else
    fail "nox.desktop not found at ${DESKTOP_FILE}"
fi

# 4. Optional Namcap Verification
info "4. Checking namcap verification tool..."
if command -v namcap >/dev/null 2>&1; then
    pass "namcap is installed, running on PKGBUILD..."
    namcap "${PKG_DIR}/PKGBUILD" || true
else
    warn "namcap not installed on system (optional tool; can be installed via 'sudo pacman -S --needed namcap')"
fi

# 5. Live System Installation Check
info "5. Checking System PATH and /opt/nox installation status..."
if [ -d "/opt/nox" ]; then
    pass "/opt/nox directory exists on live system"
    [ -x "/opt/nox/venv/bin/nx" ] && pass "/opt/nox/venv/bin/nx is executable" || warn "/opt/nox/venv/bin/nx missing"
    [ -d "/opt/nox/ui/dist" ] && pass "/opt/nox/ui/dist directory exists" || warn "/opt/nox/ui/dist missing"
else
    warn "/opt/nox is not yet installed on this machine."
    warn "To install the built packages, run:"
    warn "  USER-RUN: sudo pacman -U ${PKG_DIR}/*.pkg.tar.zst"
fi

for cmd in nx noxd nox-app; do
    if command -v "$cmd" >/dev/null 2>&1; then
        pass "Command '$cmd' is available on PATH at $(command -v "$cmd")"
    else
        warn "Command '$cmd' not yet on system PATH (available once installed with pacman -U)"
    fi
done

# 6. Summary
echo ""
if [ "$ERRORS" -eq 0 ]; then
    echo -e "${GREEN}${BOLD}✓ Arch Linux Packaging Verification Successful!${RESET}\n"
    exit 0
else
    echo -e "${RED}${BOLD}✗ Verification Failed with ${ERRORS} error(s).${RESET}\n"
    exit 1
fi
