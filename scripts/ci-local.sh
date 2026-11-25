#!/usr/bin/env bash
# scripts/ci-local.sh
# Local CI runner mirroring GitHub Actions workflows (.github/workflows/ci.yml)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "=================================================="
echo "== NoX Local CI Runner                          =="
echo "=================================================="

# 1. Python Lint
echo "==> [1/5] Running Ruff Linter..."
uv run ruff check src/noxai/connectors/ src/noxai/channels/ src/noxai/agents/ src/noxai/security/ src/noxai/cli.py

# 2. Python Tests (CI Core Suite)
echo "==> [2/5] Running Python Test Suite..."
uv run pytest tests/test_security_foundation.py tests/test_mail_connectors.py tests/test_channels_and_feeds.py tests/test_github_intelligence.py tests/test_agents_framework.py tests/test_doctor.py tests/test_repo_hygiene.py -v --timeout=60

# 3. UI Vitest Suite
echo "==> [3/5] Running Vitest Suite..."
npm --prefix ui test -- --run

# 4. UI Production Build
echo "==> [4/5] Building UI Production Bundle..."
npm --prefix ui run build

# 5. Repo Hygiene & Test Integrity Audits
echo "==> [5/5] Checking Test & Safety Audits..."
bash scripts/check-test-changes.sh

echo "=================================================="
echo "== ALL LOCAL CI CHECKS PASSED SUCCESSFULLY      =="
echo "=================================================="
