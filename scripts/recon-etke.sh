#!/usr/bin/env bash
# Read-only recon of the etke.cc Matrix host. Gathers the facts that gate writing
# the compose/ stack and the export. Makes NO changes. Run on the etke box (or via ssh).
#
#   ssh etke 'bash -s' < scripts/recon-etke.sh
#
set -uo pipefail
say() { printf '\n=== %s ===\n' "$1"; }

say "Synapse version"
docker exec matrix-synapse python -m synapse.app.homeserver --version 2>/dev/null \
  || docker exec matrix-synapse synapse_homeserver --version 2>/dev/null \
  || echo "(adjust container name; try: docker ps --format '{{.Names}}' | grep -i synapse)"

say "Postgres major version"
docker exec matrix-postgres postgres --version 2>/dev/null \
  || echo "(adjust container name; try: docker ps --format '{{.Names}}' | grep -i postgres)"

say "Synapse DB size"
docker exec matrix-postgres psql -U synapse -d synapse -tAc \
  "SELECT pg_size_pretty(pg_database_size('synapse'));" 2>/dev/null || echo "(check creds/db name)"

say "Media store size"
du -sh /matrix/synapse/storage/media-store 2>/dev/null || echo "(path differs?)"

say "Appservice registrations (bridges/bots to carry later)"
ls -1 /matrix/*/registration.yaml /matrix/synapse/config/*registration*.yaml 2>/dev/null || echo "(none found at default paths)"
grep -rEl '^id:|^as_token:' /matrix --include='*registration*.yaml' 2>/dev/null

say "Secrets to carry IDENTICALLY (do not print to a shared log!)"
echo "Pull these from /matrix/synapse/config/homeserver.yaml on the box yourself:"
echo "  signing_key (file: homeserver.signing.key), macaroon_secret_key, form_secret, registration_shared_secret"

say "Deliberate homeserver.yaml overrides worth keeping"
grep -nE '^(rc_|retention|federation_(domain|allow)|url_preview|enable_registration|max_upload_size|presence)' \
  /matrix/synapse/config/homeserver.yaml 2>/dev/null || echo "(none / path differs)"

say "Running matrix containers (full inventory)"
docker ps --format 'table {{.Names}}\t{{.Image}}' | grep -iE 'matrix|synapse|draupnir|mautrix|bridge|bot|coturn' || true

say "DONE — paste this output back (redact the secrets section)"
