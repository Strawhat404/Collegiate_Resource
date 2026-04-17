#!/usr/bin/env bash
# ============================================================
# CRHGC — Unified Test Runner
# Usage: ./run_tests.sh
#
# When called from outside a container, delegates to the
# dedicated 'test' service (docker compose --profile test run)
# which has all deps pre-installed and no network_mode/name
# conflicts with the main app container.
#
# When called from inside the container (/app/main.py exists),
# runs the test suite directly using the container's Python.
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# -----------------------------------------------------------
# INSIDE the container — run the suite directly.
# Detected by /app/main.py which only exists in the built image.
# -----------------------------------------------------------
if [ -f /app/main.py ]; then
    PYTHON="python3"
    PASS=0
    FAIL=0

    echo "=========================================="
    echo "  CRHGC Test Suite  (container runtime)"
    echo "=========================================="
    echo ""

    # ---- Headless verification (service flows) ----
    echo "=== Headless Verification (verify.py) ==="
    if "$PYTHON" /app/verify.py; then
        PASS=$((PASS + 1))
        echo "PASS: verify.py"
    else
        FAIL=$((FAIL + 1))
        echo "FAIL: verify.py"
    fi
    echo ""

    # ---- pytest suite ----
    echo "=== Unit / API / E2E Tests (pytest) ==="
    if "$PYTHON" -m pytest /app -q --tb=short; then
        PASS=$((PASS + 1))
        echo "PASS: pytest"
    else
        FAIL=$((FAIL + 1))
        echo "FAIL: pytest"
    fi
    echo ""

    # ---- Summary ----
    echo "=========================================="
    echo "  Test Summary"
    echo "=========================================="
    echo "Suites passed: $PASS"
    echo "Suites failed: $FAIL"
    echo "=========================================="

    if [ "$FAIL" -gt 0 ]; then
        echo "RESULT: SOME TESTS FAILED"
        exit 1
    else
        echo "RESULT: ALL TESTS PASSED"
        exit 0
    fi
fi

# -----------------------------------------------------------
# OUTSIDE the container — delegate to the 'test' profile
# service which is purpose-built for headless test execution
# and has no container_name or network_mode conflicts.
# -----------------------------------------------------------
echo "Delegating test execution to Docker test service..."

docker compose --profile test run --rm test
exit $?
