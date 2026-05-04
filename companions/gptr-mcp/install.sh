#!/usr/bin/env bash
# Installer for the gptr-mcp companion.
#
# Clones the upstream gptr-mcp project into a sibling directory next to the
# parent repo and builds a Python venv. Does not modify the parent repo.
#
# Usage:
#   cd companions/gptr-mcp
#   ./install.sh

set -euo pipefail

SCRIPT_DIR="$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
COMPANIONS_DIR="$( dirname "$SCRIPT_DIR" )"
PARENT_REPO="$( dirname "$COMPANIONS_DIR" )"
PARENT_PARENT="$( dirname "$PARENT_REPO" )"

UPSTREAM_URL="https://github.com/assafelovic/gptr-mcp.git"
# Default ref. Override with `GPTR_MCP_REF=v0.x.y ./install.sh` to pin to a
# specific tag or commit. Default is `main` because the upstream does not yet
# publish stable tagged releases — accept that re-running the script may pull
# breaking changes and pin a ref if you need reproducibility.
GPTR_MCP_REF="${GPTR_MCP_REF:-main}"
TARGET_DIR="$PARENT_PARENT/gptr-mcp-source"

echo "Installing gptr-mcp companion"
echo "  Source clone target: $TARGET_DIR"
echo "  Upstream:            $UPSTREAM_URL"
echo "  Ref:                 $GPTR_MCP_REF"
echo

if [ -d "$TARGET_DIR" ]; then
  echo "Target directory already exists. Fetching + checking out $GPTR_MCP_REF..."
  cd "$TARGET_DIR"
  git fetch --tags origin
  git checkout "$GPTR_MCP_REF"
  # Only fast-forward if we are on a branch (not a detached tag/commit)
  if git symbolic-ref -q HEAD >/dev/null; then
    git pull --ff-only
  fi
else
  echo "Cloning upstream..."
  git clone "$UPSTREAM_URL" "$TARGET_DIR"
  cd "$TARGET_DIR"
  git checkout "$GPTR_MCP_REF"
fi

echo
echo "Building venv..."
if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo
echo "✓ gptr-mcp installed at $TARGET_DIR"
echo
echo "Next steps:"
echo "  1. cp $SCRIPT_DIR/env.example $TARGET_DIR/.env"
echo "  2. Edit $TARGET_DIR/.env — set OPENAI_API_KEY and TAVILY_API_KEY"
echo "  3. Register the MCP with Claude Code per $SCRIPT_DIR/README.md"
echo "  4. Smoke test: $TARGET_DIR/.venv/bin/python $TARGET_DIR/server.py < /dev/null"
