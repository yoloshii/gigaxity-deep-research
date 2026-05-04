#!/bin/bash
# Run test suite with various options

set -e

cd "$(dirname "$0")/.."

# Load test environment
if [ -f tests/.env ]; then
    export $(grep -v '^#' tests/.env | xargs)
fi

case "${1:-all}" in
    unit)
        echo "Running unit tests..."
        pytest -m unit
        ;;
    integration)
        echo "Running integration tests..."
        pytest -m integration
        ;;
    slow)
        echo "Running slow tests (LLM)..."
        pytest -m slow
        ;;
    quick)
        echo "Running quick tests (unit + integration, no slow)..."
        pytest -m "not slow"
        ;;
    all)
        echo "Running all tests..."
        pytest
        ;;
    coverage)
        echo "Running tests with coverage..."
        pytest --cov=src --cov-report=html --cov-report=term-missing
        ;;
    *)
        echo "Usage: $0 {unit|integration|slow|quick|all|coverage}"
        exit 1
        ;;
esac
