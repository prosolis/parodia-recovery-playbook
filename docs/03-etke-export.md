# 03 — Exporting data from etke.cc

Goal: pull a complete, restorable snapshot of the homeserver. The three things that
make the cutover *seamless* (vs. a fresh server) are the **signing key**, the
**Postgres dump**, and the **media store**. Everything else is config we can rebuild.

## Access

etke.cc is built on `matrix-docker-ansible-deploy` and runs everything under `/matrix`
on the managed host. Two routes:

- **You have SSH to the etke box** → export directly (preferred; commands below).
- **You don't** → request an offboarding/full backup from etke support; they provide a
  tarball. Confirm it includes the signing key, a Postgres dump, and the media store.

> Do the export against a **quiesced** server during the cutover window to avoid a
> moving target (see `04-cutover-runbook.md`). A pre-export *dry run* while live is fine
> for sizing and rehearsal — just don't treat that dump as the final one.

## 1. Signing key (most important)

```bash
# on the etke host — the actual filename is server-name-prefixed:
cat /matrix/synapse/config/matrix.parodia.dev.signing.key
# → copy verbatim to the new host. One line (59 bytes). Guard it like a secret.
#   Our homeserver.yaml sets signing_key_path: /data/homeserver.signing.key, so save it as
#   compose/synapse/homeserver.signing.key (renaming is fine — content is the identity).
```

Also capture these secrets from `/matrix/synapse/config/homeserver.yaml` and keep them
**identical** on the new host (preserves existing access tokens & login sessions):
`macaroon_secret_key`, `form_secret`, `registration_shared_secret`.

## 2. Postgres dump

```bash
# etke creds (confirmed 2026-06-28): container matrix-postgres, user+db both `synapse`.
docker exec matrix-postgres pg_dump -U synapse -Fc synapse > synapse.dump
# -Fc = custom format (compressed, restorable with pg_restore).
docker exec matrix-postgres postgres --version   # confirmed: PostgreSQL 18.4
```

> **PG 18 → target must be PG ≥ 18.** The new stack pins `postgres:18-alpine`. Do NOT
> restore this dump into 17/16 — `pg_restore` refuses a downgrade. DB is ~2.9 G (fast).

## 3. Media store

```bash
# Stop write churn first if possible. rsync is resumable for the big transfer.
rsync -aHAX --info=progress2 \
  /matrix/synapse/storage/media-store/ \
  parodia.dev:/srv/matrix/synapse/media-store/
```

## 4. Appservice registrations (for later bridge/bot phase)

```bash
ls /matrix/*/registration.yaml /matrix/synapse/config/*registration*.yaml 2>/dev/null
# Confirmed 2026-06-28: the ONLY registration is /matrix/appservice-double-puppet/config/
# registration.yaml — a vestigial mautrix helper (no bridges deployed). Draupnir runs as a
# normal bot USER (@draupnir:parodia.dev), not an appservice. Both are deferred/dropped for
# the core move; copy the file only if you later re-add mautrix bridges with double-puppeting.
```

## 4b. coturn / TURN secret (calls are in the core stack)

Do **not** carry etke's `turn_shared_secret` — generate a fresh one (`openssl rand -hex 32`)
and put the SAME value in both `compose/.env` (`TURN_SHARED_SECRET`) and Synapse's
`turn_shared_secret`. coturn realm/ports we re-author (realm `parodia.dev`, relay
49152–49252); `external-ip` becomes the new box `178.104.56.222`.

## 5. Config reference (rebuild, don't copy wholesale)

Grab `homeserver.yaml` for *reference* — copy the secrets above and any deliberate
overrides (retention, rate limits, federation allow/deny, URL preview, registration),
but **drop** all etke/ketesa-specific blocks. We author a clean `homeserver.yaml` in
`compose/`.

## Export checklist

- [ ] `homeserver.signing.key`
- [ ] `macaroon_secret_key`, `form_secret`, `registration_shared_secret`
- [ ] `synapse.dump` (+ noted PG major version)
- [ ] `media-store/` synced
- [ ] appservice registration files
- [ ] `homeserver.yaml` (reference) + list of deliberate overrides
- [ ] coturn/TURN secret (if VoIP in use)
- [ ] noted Synapse version
