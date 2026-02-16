#!/usr/bin/env bash
set -euo pipefail

# Run the birdnet-analytics web app, ensuring env is loaded.
#
# Usage:
#   ./scripts/run_dev.sh [env-file]
#
# Default env file:
#   configs/local.env (if present) else configs/ubuntuplex.env (if present)

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

ENV_FILE="${1:-}"
if [[ -z "$ENV_FILE" ]]; then
  if [[ -f "configs/local.env" ]]; then
    ENV_FILE="configs/local.env"
  elif [[ -f "configs/ubuntuplex.env" ]]; then
    ENV_FILE="configs/ubuntuplex.env"
  else
    echo "No env file found. Create one of:" >&2
    echo "  - configs/local.env (copy from configs/local.env.example)" >&2
    echo "  - configs/ubuntuplex.env (copy from configs/ubuntuplex.env.example)" >&2
    exit 2
  fi
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Env file not found: $ENV_FILE" >&2
  exit 2
fi

# shellcheck disable=SC1090
set -a
source "$ENV_FILE"
set +a

: "${BIRDNET_ANALYTICS_HOST:=0.0.0.0}"
: "${BIRDNET_ANALYTICS_PORT:=8787}"

exec uv run uvicorn --app-dir src birdnet_analytics.web:app \
  --reload \
  --host "$BIRDNET_ANALYTICS_HOST" \
  --port "$BIRDNET_ANALYTICS_PORT"
