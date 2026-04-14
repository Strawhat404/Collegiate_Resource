#!/usr/bin/env bash
# ============================================================
# CRHGC — Unified Test Runner
# Usage: ./run_tests.sh
# If running outside Docker, delegates to Docker automatically.
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# If not inside a container, run via Docker to ensure all deps are available
if [ ! -f /.dockerenv ]; then
    echo "Not running inside Docker — delegating to Docker container..."
    docker compose run --rm \
        -e DISPLAY="" \
        -e QT_QPA_PLATFORM=offscreen \
        app bash run_tests.sh
    exit $?
fi

PYTHON="${PYTHON:-python3}"
PASS=0
FAIL=0

echo "=========================================="
echo "  CRHGC Test Suite"
echo "=========================================="
echo ""

# ---- Headless verification (17 service flows) ----
echo "=== Headless Verification (verify.py) ==="
if "$PYTHON" verify.py; then
    PASS=$((PASS + 1))
    echo "  PASS: verify.py"
else
    FAIL=$((FAIL + 1))
    echo "  FAIL: verify.py"
fi
echo ""

# ---- Unit tests ----
echo "=== Unit Tests (tests/) ==="
if command -v pytest &>/dev/null; then
    if pytest -q --tb=short; then
        PASS=$((PASS + 1))
        echo "  PASS: pytest"
    else
        FAIL=$((FAIL + 1))
        echo "  FAIL: pytest"
    fi
else
    if "$PYTHON" tests/run_all.py; then
        PASS=$((PASS + 1))
        echo "  PASS: tests/run_all.py"
    else
        FAIL=$((FAIL + 1))
        echo "  FAIL: tests/run_all.py"
    fi
fi
echo ""

# ---- Summary ----
echo "=========================================="
echo "  Test Summary"
echo "=========================================="
echo "  Suites passed: $PASS"
echo "  Suites failed: $FAIL"
echo "=========================================="

if [ "$FAIL" -gt 0 ]; then
    echo "RESULT: SOME TESTS FAILED"
    exit 1
else
    echo "RESULT: ALL TESTS PASSED"
    exit 0
fi
