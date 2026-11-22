#!/usr/bin/env bash
# scripts/check-test-changes.sh
# Verifies that every modified test file is documented in docs/audits/test-and-safety-changes.md

set -euo pipefail

BASE_REF="${1:-origin/main}"
AUDIT_DOC="docs/audits/test-and-safety-changes.md"
SESSION_M_AUDIT="docs/audits/session-m-test-changes.md"

if [ ! -f "$AUDIT_DOC" ]; then
    echo "ERROR: Missing audit document $AUDIT_DOC"
    exit 1
fi

# Find all modified test files compared to base ref
CHANGED_TESTS=$(git diff --diff-filter=M --name-only "$BASE_REF"...HEAD tests/ | grep -E '^tests/test_.*\.py$' || true)

FAILED=0
for test_file in $CHANGED_TESTS; do
    base_name=$(basename "$test_file")
    if ! grep -q "$base_name" "$AUDIT_DOC" && ! grep -q "$base_name" "$SESSION_M_AUDIT"; then
        echo "FAIL: Test file $test_file was modified but is NOT documented in $AUDIT_DOC"
        FAILED=1
    else
        echo "OK: $test_file documented in test changes audit."
    fi
done

if [ "$FAILED" -ne 0 ]; then
    echo "Test audit check failed. Document all test modifications in $AUDIT_DOC before committing."
    exit 1
fi

echo "All modified test files are properly documented."
exit 0
