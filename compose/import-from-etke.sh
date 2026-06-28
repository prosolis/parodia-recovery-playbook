#!/usr/bin/env bash
# Restore an etke export into the clean compose stack. Run from compose/.
# Order (see docs/04): postgres up -> restore DB -> media in place -> THEN synapse up.
#
#   ./import-from-etke.sh /path/to/synapse.dump /path/to/media-store/
#
set -euo pipefail
DUMP="${1:?usage: import-from-etke.sh <synapse.dump> <media-store-dir>}"
MEDIA="${2:?usage: import-from-etke.sh <synapse.dump> <media-store-dir>}"

echo ">> Bringing up a clean Postgres (C collation enforced at initdb)…"
docker compose up -d postgres
until docker compose exec -T postgres pg_isready -U synapse -d synapse >/dev/null 2>&1; do
  echo "   waiting for postgres…"; sleep 2
done

echo ">> Sanity-check the new DB collation is C (Synapse requires it)…"
docker compose exec -T postgres psql -U synapse -d synapse -tAc \
  "SELECT datcollate FROM pg_database WHERE datname='synapse';"

echo ">> Restoring the dump (pg_restore tolerates older->newer PG major)…"
docker compose exec -T postgres pg_restore -U synapse -d synapse --clean --if-exists --no-owner < "$DUMP"

echo ">> Placing media store…"
mkdir -p ./data/media-store
rsync -aHAX --info=progress2 "${MEDIA%/}/" ./data/media-store/

cat <<'EOF'

>> DB + media imported. Before starting Synapse, confirm:
   - compose/synapse/homeserver.signing.key  (copied verbatim from etke)
   - macaroon_secret_key / form_secret / registration_shared_secret  filled in homeserver.yaml
Then:
   docker compose up -d synapse
   docker compose logs -f synapse     # watch schema migrations apply on first boot
EOF
