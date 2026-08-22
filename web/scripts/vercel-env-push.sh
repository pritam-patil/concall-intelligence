#!/usr/bin/env bash
# Push every variable in web/.env.local (or the file given as $1) to the
# linked Vercel project's Production environment, one `vercel env add` per
# variable, reading each value from stdin so it never appears in a process
# list or shell history. Existing values are overwritten (--force).
#
# Runs from the REPO ROOT, where the Vercel CLI link (.vercel/) lives — the
# project's Root Directory is web/, so the CLI is linked at the root.
#
#   vercel link --project concall-intelligence   # once, at the repo root
#   web/scripts/vercel-env-push.sh               # web/.env.local -> Production
#   VERCEL_ENV_TARGET=preview web/scripts/vercel-env-push.sh
#
# NEXT_PUBLIC_SITE_URL is skipped: on Vercel the site URL is derived from
# VERCEL_PROJECT_PRODUCTION_URL (see src/app/layout.tsx), and a localhost
# value copied from a dev .env.local would be wrong in production.
set -euo pipefail
cd "$(dirname "$0")/../.."

ENV_FILE="${1:-web/.env.local}"
TARGET="${VERCEL_ENV_TARGET:-production}"
SKIP="NEXT_PUBLIC_SITE_URL"

[ -f "$ENV_FILE" ] || { echo "no $ENV_FILE" >&2; exit 1; }

pushed=0
while IFS= read -r line || [ -n "$line" ]; do
  [[ "$line" =~ ^[[:space:]]*(#|$) ]] && continue
  [[ "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]] || continue
  name="${line%%=*}"
  value="${line#*=}"
  # Strip one layer of surrounding quotes, if any.
  if [[ "$value" =~ ^\"(.*)\"$ ]] || [[ "$value" =~ ^\'(.*)\'$ ]]; then value="${BASH_REMATCH[1]}"; fi
  if [[ " $SKIP " == *" $name "* ]]; then echo "skip  $name"; continue; fi
  # Vercel makes Production/Preview variables "sensitive" (write-only) by
  # default. Keep that for anything that looks like a secret; plain config
  # (provider names, model ids, the public URL) stays readable so
  # `vercel env pull` can still reconstruct a working .env.local.
  # (A plain string, not an array: macOS ships bash 3.2, where an empty
  # array expansion trips `set -u`.)
  sensitivity="--no-sensitive"
  [[ "$name" =~ (KEY|TOKEN|SECRET|PASSWORD) ]] && sensitivity=""
  # shellcheck disable=SC2086  # $sensitivity is intentionally word-split (one flag or nothing)
  printf '%s' "$value" | vercel env add "$name" "$TARGET" --force --yes $sensitivity >/dev/null
  echo "set   $name ($TARGET)${sensitivity:+ readable}"
  pushed=$((pushed + 1))
done < "$ENV_FILE"
echo "pushed $pushed variable(s) to $TARGET"
