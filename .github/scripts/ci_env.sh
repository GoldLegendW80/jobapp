#!/usr/bin/env bash
# Write the .env of the CI e2e stack from .env.example: every placeholder secret replaced by a
# fresh random value, caddy on 127.0.0.1:${1:-8080}. The file lives only on the CI runner.
#
#   .github/scripts/ci_env.sh [port] > .env
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
port="${1:-8080}"
[[ "$port" =~ ^[0-9]+$ ]] || {
  echo "ci_env.sh: port must be a number" >&2
  exit 1
}
pg_password="$(openssl rand -hex 32)"
secret="$(openssl rand -hex 48)"
credentials_key="$(openssl rand -base64 32)"

sed -e "s/^SECRET_KEY=.*/SECRET_KEY=$secret/" \
  -e "s#^CREDENTIALS_KEY=.*#CREDENTIALS_KEY=$credentials_key#" \
  -e "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$pg_password/" \
  -e "s#^DATABASE_URL=.*#DATABASE_URL=postgresql+psycopg://jobapp:$pg_password@db:5432/jobapp#" \
  -e "s/^HTTP_BIND=.*/HTTP_BIND=127.0.0.1/" \
  -e "s/^HTTP_PORT=.*/HTTP_PORT=$port/" \
  "$ROOT/.env.example"
