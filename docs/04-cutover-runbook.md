# 04 — Cutover runbook

Staged, **core-first** cutover with a rehearsal and a clean rollback. The whole point of
preserving the signing key + DB is that this is a *move*, not a re-registration — done
right, users keep their accounts, history, and device trust.

## Environment (confirmed on parodia.dev, 2026-06-20)

| | Value |
|---|---|
| Target box public IP | **178.104.56.222** (`www.parodia.dev`) |
| Current etke IP | 46.225.142.216 (where `matrix.parodia.dev` + apex point today) |
| Proxy | mash-traefik, network `traefik`, entrypoint `web-secure`, certResolver `default` |
| Disk free | 215 G (ample for DB + media) |
| Docker / Compose | 29.3.0 / v5.1.0 — `compose config` validates |
| Stack staged at | `~/matrix/compose` on the box (not yet started) |

## Phases

1. **Build & rehearse** (no DNS change) — stand up the new stack, import a *dry-run*
   dump, smoke test against a throwaway hostname or `/etc/hosts` override.
2. **Cutover** (maintenance window) — quiesce etke, final export, import, flip DNS/proxy.
3. **Post-cutover** — verify federation, then bridges/bots, then MAS + Authentik.

---

## Phase 1 — Build & rehearse

Build the **plumbing** first and prove it boots, *then* land the data. Order matters:
restore the dump into a **clean** DB and boot Synapse against *that* — if Synapse fully
starts on an empty DB it initializes its own schema and you'd have to drop/recreate before
restoring.

- [ ] Author `compose/` stack: Synapse + dedicated Postgres + Element + static well-known.
      Use **current images** — `latest` Synapse is ≥ etke's by definition; PG 16/17 both fine.
- [ ] Bring up Postgres (clean, `LC_COLLATE=C`) and confirm the stack's config/networking is sane.
- [ ] Drop in the signing key + the three secrets (`macaroon_secret_key`, `form_secret`,
      `registration_shared_secret`) **before** Synapse's first real boot.
- [ ] Restore the **dry-run** DB into the clean Postgres (`pg_restore`) + rsync media,
      *then* start Synapse so it migrates the imported schema forward on first boot.
- [ ] Smoke test without touching public DNS:
  - Resolve `matrix.parodia.dev` to the new host via local `/etc/hosts`, OR use a temp hostname.
  - `curl https://<new>/_matrix/client/versions`
  - Log in with a real account, confirm rooms/history render.
- [ ] Decide reverse-proxy front (mash-traefik vs. own) and wire it.

## Phase 2 — Cutover (maintenance window)

Announce downtime. Federation tolerates a short outage; aim to minimize it.

1. **Quiesce etke** — stop Synapse on etke (or set read-only) so the DB/media stop moving.
2. **Final export** — fresh `pg_dump` + final `rsync` media delta (resumable, so it's quick).
3. **Import** into the new stack; start Synapse; local smoke test (as Phase 1).
4. **Flip delegation/DNS:**
   - Point `matrix.parodia.dev` A record: `46.225.142.216` (etke) → **`178.104.56.222`** (this box).
   - `.well-known/matrix/server` → `{"m.server":"matrix.parodia.dev:443"}` (we chose 443-only).
   - `.well-known/matrix/client` → clean homeserver block (drop ketesa/etke/vector.im).
   - **Apex caveat:** `parodia.dev` itself still points at etke (46.225.142.216), so its
     `.well-known` is served by etke until you move it. Either repoint the apex to this box
     (the `well-known` nginx service handles the path) or serve those two JSON files from
     wherever the apex lives. Decide what else `parodia.dev` apex should serve.
5. **Verify** (below).

## Verify

- [ ] [Federation Tester](https://federationtester.matrix.org/#parodia.dev) — all green
- [ ] `curl https://matrix.parodia.dev/_matrix/client/versions`
- [ ] `.well-known/matrix/server` and `.../client` return the new clean values
- [ ] Existing user logs in **without** re-auth (proves signing key + secrets carried over)
- [ ] Send a message to a remote room (federation send works)
- [ ] Receive from a remote room (federation receive works)
- [ ] Media: old images still load; new upload works
- [ ] Signing key fingerprint matches the old server (`/_matrix/key/v2/server`)

## Rollback

Until DNS TTL expires and you're confident, keep etke **paused, not deleted**.

- Revert `matrix.parodia.dev` A/AAAA + `.well-known` to etke.
- Because etke was only quiesced (not torn down), it resumes as the source of truth.
- Keep DNS TTL low (e.g. 300s) through the window so rollback propagates fast.
- **Don't** cancel etke service until federation + logins are verified stable for a few days.

## Then (later phases, separate docs)

- Re-add appservices (Draupnir first) from the saved registration files.
- Stand up **MAS + Authentik OIDC** (the original auth goal) — net-new, planned separately.
- Bridges/bots as needed.

## Authentik prep notes (parodia.dev)

Authentik runs separately at `authentik.parodia.dev` (`/mash/authentik/compose.yml`,
MASH-style layout, image `ghcr.io/goauthentik/server:2026.5.3`).

- **Media persistence (fixed 2026-06-21).** The MASH compose mounted only `./data`,
  `./certs`, `./custom-templates`, and blueprints — **no `media` mount**. Uploaded
  application icons land in `/media/application-icons/`, which was *ephemeral* (container
  writable layer), so they'd be lost on any recreate/upgrade. Added `- ./media:/media` to
  **both** `server` and `worker`, created `/mash/authentik/media` (host uid 1000 →
  in-container `authentik` user), recreated both services. Backup of the pre-change compose:
  `compose.yml.bak-media-20260621-204902`.
  - When adding the **Matrix / MAS** application here, its dashboard icon can now be
    uploaded directly and will persist. URL-mode icons also work and need no media mount.
