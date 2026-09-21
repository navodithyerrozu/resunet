#!/usr/bin/env bash
#
# Regenerate packages/shared/src/generated/api.ts from the FastAPI schema.
#
# The schema is dumped by importing the app and calling `.openapi()`, not by
# curling a running server. That matters for CI: there is no container to boot,
# no port to wait on, and no chance of generating types against a stale process
# that is still running last week's code.
#
# Usage:
#   pnpm gen:types           # rewrite the committed file
#   pnpm gen:types --check   # fail if the committed file is out of date
#
# The --check mode is what makes schema drift a build error. Run it in CI after
# any backend change; a green check means the TypeScript the frontend compiles
# against still describes what the API actually returns.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_DIR="$REPO_ROOT/apps/api"
OUT="$REPO_ROOT/packages/shared/src/generated/api.ts"
SCHEMA="$(mktemp -t resunet-openapi-XXXXXX.json)"
trap 'rm -f "$SCHEMA"' EXIT

CHECK_ONLY=0
[[ "${1:-}" == "--check" ]] && CHECK_ONLY=1

command -v python >/dev/null 2>&1 && PY=python || PY=python3

echo "==> dumping OpenAPI schema from the FastAPI app"
(
  cd "$API_DIR"
  # PYTHONPATH so `app` resolves whether or not the package is pip-installed.
  PYTHONPATH="$API_DIR:${PYTHONPATH:-}" "$PY" - <<'PYCODE' > "$SCHEMA"
import json
import sys

# Importing the app runs module-level config. Anything that would reach out to
# Mongo or Redis belongs in the lifespan handler, not at import time -- if this
# line ever starts hanging, that assumption has been broken and the fix is in
# the app, not here.
from app.main import app

schema = app.openapi()
json.dump(schema, sys.stdout, indent=2, sort_keys=True)
PYCODE
)

echo "==> generating TypeScript"
GENERATED="$(mktemp -t resunet-api-XXXXXX.ts)"
trap 'rm -f "$SCHEMA" "$GENERATED"' EXIT

HEADER='/**
 * GENERATED FILE -- DO NOT EDIT BY HAND.
 *
 * Produced by `scripts/gen-types.sh` from the FastAPI OpenAPI schema. Pydantic
 * is the single source of truth; this file is a projection of it. Run
 * `pnpm gen:types` after any backend schema change, and `pnpm gen:types --check`
 * in CI to make drift a build failure.
 */
'

{
  printf '%s\n' "$HEADER"
  pnpm --silent exec openapi-typescript "$SCHEMA" \
    --root-types \
    --alphabetize \
    --empty-objects-unknown
} > "$GENERATED"

if [[ "$CHECK_ONLY" == "1" ]]; then
  if diff -u "$OUT" "$GENERATED" > /dev/null 2>&1; then
    echo "==> generated types are up to date"
    exit 0
  fi
  echo "!!! packages/shared/src/generated/api.ts is out of date." >&2
  echo "!!! The API schema changed but the committed types did not." >&2
  echo "!!! Run 'pnpm gen:types' and commit the result." >&2
  echo >&2
  diff -u "$OUT" "$GENERATED" >&2 || true
  exit 1
fi

mv "$GENERATED" "$OUT"
echo "==> wrote $OUT"
echo "==> reminder: 'pnpm --filter @resunet/shared typecheck' will tell you if"
echo "    any alias in src/index.ts no longer matches a schema."
