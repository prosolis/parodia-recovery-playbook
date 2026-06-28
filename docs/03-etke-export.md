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
# on the etke host
cat /matrix/synapse/config/homeserver.signing.key
# → copy verbatim to the new host as the same filename. One line. Guard it like a secret.
```

Also capture these secrets from `/matrix/synapse/config/homeserver.yaml` and keep them
**identical** on the new host (preserves existing access tokens & login sessions):
`macaroon_secret_key`, `form_secret`, `registration_shared_secret`.

## 2. Postgres dump

```bash
# Find the synapse DB container/creds (etke: matrix-postgres)
docker exec matrix-postgres pg_dump -U synapse -Fc synapse > synapse.dump
# -Fc = custom format (compressed, restorable with pg_restore). Note the PG major version:
docker exec matrix-postgres postgres --version
```

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
# Draupnir at minimum. Copy each; they pin the as_token/hs_token bots authenticate with.
```

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
