#!/usr/bin/env bash
# ============================================================
# CRHGC — Unified Test Runner
# Usage: ./run_tests.sh
#
# Always delegates test execution into the Docker container
# where all runtime dependencies (Python, pytest, cryptography,
# PyQt6, Xvfb) are pre-installed via requirements.txt.
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# -----------------------------------------------------------
# If we are already running INSIDE our app container
# (detected by the presence of /app/main.py which is only
# copied during the Docker build), run the suite directly.
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
# Outside the app container — spin up a fresh container via
# docker compose run so all deps are available regardless of
# whether the app container is currently running or not.
# -----------------------------------------------------------
echo "Delegating test execution into Docker container..."

docker compose run --rm \
    -e DISPLAY=:99 \
    -e QT_QPA_PLATFORM=offscreen \
    --entrypoint bash \
    app -c "
        rm -f /tmp/.X99-lock &&
        Xvfb :99 -screen 0 1280x720x24 -nolisten tcp &
        sleep 1 &&
        DISPLAY=:99 QT_QPA_PLATFORM=offscreen bash /app/run_tests.sh
    "
exit $?
