# 06 — Parodia.dev restoration playbook

Disaster-recovery runbook for the `parodia.dev` host. Written as a **pre-req to the
Matrix cutover** (`04-cutover-runbook.md`): once Synapse lands here, its signing key is
irreplaceable, so we want proven recovery *before* the crown jewel arrives.

> **One-line summary:** the host is a Hetzner Cloud VM with Hetzner's automated whole-VM
> backups, plus an age-encrypted offsite S3 layer (§5) for per-service recovery. The gaps
> Hetzner backups *don't* cover are in [§4](#4-what-hetzner-backups-do-not-cover-the-gaps).

### Pick your scenario

| What happened | Go to |
|---|---|
| **Whole host lost** — disk/hardware failure, box gone | **Runbook A (§6)** — restore the Hetzner whole-VM snapshot |
| **Whole host lost, no usable snapshot** — region loss, provider migration | **Runbook B (§7)** — rebuild a fresh box from the offsite S3 bundle |
| **One service fell over** — crashed, corrupted DB, bad config/upgrade | **Runbook C (§8)** — restart or restore just that service |

---

## 1. The box (confirmed 2026-06-28)

| | Value |
|---|---|
| Provider / hostname | **Hetzner Cloud**, `parodia-24gb-nbg1-1` (id `123765145`) |
| Server type / DC | **`cpx52`** (shared vCPU, 12 core / 24 GB), datacenter **`nbg1-dc3`** (Nuremberg) |
| Disk / RAM | single 451 G root disk (225 G used) — **no attached volumes** (all state on root); 22 Gi RAM, **no swap** |
| OS | Debian 13 (trixie), kernel 6.12 |
| Docker / Compose | 29.3.0 / v5.1.0 |
| Public IP | **178.104.56.222** (`www.parodia.dev`); no floating IP |
| SSH | `reala@www.parodia.dev` (`reala` is sudoer; `/mash` is `mash`-owned 700/750) |
| Backups | **Hetzner Cloud Backups ENABLED** ✅ (verified 2026-06-28 via API) — daily, window `10-14` UTC, **7 rotating slots full** (oldest `06-22`, freshest `06-28T10:29`); ~161 GB whole-VM images |
| DNS | **EuroDNS** (`ns1-4.eurodns.com`) — *separate control plane, not on the VM* |
| Backup-state token | read-only hcloud API token at `/etc/parodia/hcloud-ro.token` (0600) — re-run the §12 check anytime |

## 2. What's running (services to restore, in dependency order)

Bring infra up first, then apps. Everything is Docker Compose; there is **no Ansible**
(the box was hand-converted off mash-playbook — the `/mash/*` paths are legacy layout, not
live Ansible).

| Order | Service | Compose dir | State lives in | Own DB? |
|---|---|---|---|---|
| 1 | **traefik** (reverse proxy, ACME certs) | `/mash/traefik` | bind config + ACME store | — |
| 1 | **shared-postgres** (`postgres:18`) | `/opt/shared-postgres` | volume / bind | shared DB host |
| 1 | exim-relay (outbound SMTP) | `/opt/exim-relay` | stateless | — |
| 1 | socket-proxy, watchtower | `/opt/watchtower`, mash | stateless | — |
| 2 | **Authentik** (SSO — *gates the others' logins*) | `/mash/authentik/compose.yml` | `authentik_database` vol + `data/` (blueprints, media, certs) | own `postgres:16` + redis |
| 3 | **Akkoma** (`social.`) | `/mash/akkoma` | `akkoma-db` (pgdata **uid 70**), `akkoma_uploads`/`_static` vols | own DB |
| 3 | **Lemmy** (`lemmy.`) | `/mash/lemmy` | `lemmy_postgres-data`, `lemmy_pictrs-data` vols | own `postgres:16` |
| 3 | **Gitea** (`git.`) | `/opt/gitea` | bind data | shared-postgres |
| 3 | **Miniflux** (`reader.`) | `/opt/miniflux` | **stateless** — all in shared-postgres | shared-postgres |
| 3 | WriteFreely | `/opt/writefreely` | `mash-writefreely-data` vol | — |
| 3 | Ditto / pete / pastel / veola / pentarou | `/opt/*` | per-app | shared-postgres / per-app |
| 3 | uptime-kuma | `/opt/uptime-kuma` | bind data | — |

> Authentik is the **chokepoint**: Lemmy, Akkoma, Miniflux, Gitea all use it for OIDC SSO.
> If Authentik is down or restored to a stale state, users can't log into the others. Restore
> and verify Authentik *before* declaring the apps healthy.

## 3. The crown-jewel state (irreplaceable — verify these survive any restore)

A whole-VM snapshot captures all of this; the offsite layer in §5 exists so it isn't the
*only* copy.

- **Databases** (3 separate Postgres instances + the shared one):
  `authentik`, `lemmy`, `akkoma`, and the shared-postgres DBs (miniflux, gitea, ditto, …).
- **Secrets / `.env`** across `/mash/*` and `/opt/*` — Authentik `SECRET_KEY`, DB creds,
  OIDC client secrets, exim relay creds, Akkoma `config/prod.secret.exs` (+ `SIGNING_KEY.pub`).
- **Authentik `data/`** — custom blueprints (`parodia-{enforce-mfa,invite-enrollment,recovery,theme}.yaml`),
  uploaded app icons (`data/media/public/`), signing certs. *Not in the DB.*
- **Traefik ACME store** (`acme.json`) — certs; re-issuable but rate-limited, so keep it.
- **Akkoma source tree** at `/mash/akkoma` — runs from a git checkout with **tracked-file
  patches re-applied by hand** (`endpoint.ex` RewriteOn, `config.exs` loginMethod). See
  the akkoma memory; a fresh `git reset` loses them.
- *(Future)* **Synapse `homeserver.signing.key`** once Matrix lands — losing it permanently
  breaks federation + device trust. This is *the* reason DR must be solid before cutover.

## 4. What Hetzner backups do **not** cover (the gaps)

Hetzner Cloud Backups are excellent for "the hardware died," but understand the edges:

1. **Crash-consistent, not application-consistent.** Snapshots are taken while the VM runs.
   Postgres normally replays WAL and comes up clean, but a snapshot mid-write is not a
   guaranteed-clean logical backup. → §5 logical dumps are the app-consistent layer.
2. **Same provider, same region.** A Hetzner-wide / `nbg1` incident, or account
   loss/compromise, takes the backups with the box. → §5 pushes copies offsite.
3. **7-day window only.** Corruption (or a bad migration) you don't notice within ~7 days
   is unrecoverable from Hetzner alone.
4. **Coarse granularity.** You restore the *whole VM* to a point in time. You cannot pull
   "just yesterday's Authentik DB" without spinning up the entire image. → §5 dumps are
   individually restorable.
5. **Not on the VM at all:**
   - **DNS** (EuroDNS) — A/AAAA, well-known delegation, MX. Export the zone separately.
   - **Hetzner account access** — keep recovery/2FA for the Hetzner console offline; you
     restore *through* it.
   - **Floating IP assignment** (if any) and firewall rules — Cloud-project config, not disk.
   - **This repo / playbook itself.** Recovery instructions must live **off the host**: the
     canonical remote is **GitHub** (off-host primary), mirrored to `git.parodia.dev` (Gitea)
     for convenience. The Gitea copy is *on the box being recovered*, so never rely on it
     during a real disaster — clone from GitHub.

## 5. Complementary offsite layer — `parodia-backup` (BUILT 2026-06-28 ✅)

**What already exists:** the **Ditto** app ships an in-app backup engine
(`/opt/ditto/internal/backup`, docs `backup-engine.md` / `backup-restore-plan.md`) that does
exactly the right thing for *its own* data: a consistent `pg_dump -Fc`, **age-encrypted**,
streamed to an **S3-compatible bucket**, with GFS retention and a healthchecks.io
dead-man's-snitch. Key custody is off-host — the production box holds only the age **public
recipient**, the private **identity stays offline**, so a stolen host can't read its backups.
The shared-postgres host also has a legacy `pg_dumpall` helper at `/mash/postgres/bin/dump-all`.

**The gap:** that engine covers Ditto only. **Authentik, Lemmy, Akkoma, Miniflux, Gitea,
WriteFreely** — and the non-DB secrets — have **no offsite backup at all**. `rclone`/`pg_dump`
are installed but no general remote/cron exists.

**As built — `parodia-backup`, modelled on the Ditto engine:**
- Script `/usr/local/bin/parodia-backup` (root, 0750); systemd `parodia-backup.{service,timer}`,
  **daily ~03:30 UTC** (`Persistent=true`, 15-min jitter). Enabled 2026-06-28; first manual
  run succeeded (all objects verified, `Result=success`).
- **DBs:** `pg_dump -Fc` run *inside each container* (so the dump tool always matches the
  server version — no client/server skew): `authentik`, `lemmy`, `akkoma`, and shared-postgres
  `gitea` + `miniflux`. **Ditto keeps using its own engine** (excluded here).
- **Secrets/config** (34 paths): an explicit allowlist tarball — `/etc/parodia`, every service
  `.env`/`env`, Authentik `data/{blueprints,media,certs}`, traefik config (incl. `acme.json`),
  Akkoma `prod.secret.exs`, the compose files, `ditto.toml`. Big/regenerable trees (pgdata,
  Akkoma source/`_build`, Ditto models/images, docker volumes) are deliberately **excluded**.
- Each stream is piped `pg_dump|age|rclone rcat` — **plaintext never lands on disk**. Encrypted
  to the **same age recipient as Ditto** (`/etc/parodia/age-recipient.txt`; host holds only the
  public key) and pushed to the **same bucket** `s3://parodia/` under the **`host/<stamp>/`**
  prefix (sibling to Ditto's `ditto/`). Region is derived from the endpoint host (`hel1`) —
  Hetzner OS 400s a write whose region ≠ location.
- **Retention:** prunes `host/` objects older than **14 days** (never touches `ditto/`).
- **Scope note:** DBs + secrets only. Bulk media (pict-rs, akkoma uploads, the writefreely
  volume) is **not** in this layer — it rides Hetzner whole-VM backups. Add volumes here later
  if offsite media copies are wanted.

**Still TODO (not blocking):**
- Drop a healthchecks.io URL in `/etc/parodia/backup-snitch.url` → the script then pings
  `start`/success/`/fail` so a *silently stopped* backup alerts (the run is otherwise unwatched).
- Export the EuroDNS zone and commit it (git-crypt) on change.
- **Rehearse a real restore with the OFFLINE age identity** (see the §5 recipe below) — the host cannot
  decrypt its own backups by design, so only the key-holder can prove end-to-end recovery.

### Restoring from this layer (needs the offline age identity)

```bash
# rebuild the env-only rclone remote (creds in /etc/parodia/s3-backup.env)
set -a; . /etc/parodia/s3-backup.env; set +a
LOC=$(printf '%s' "$S3_ENDPOINT" | sed -E 's#^https?://([a-z0-9]+)\..*#\1#')
export RCLONE_CONFIG=/dev/null RCLONE_CONFIG_PARODIA_TYPE=s3 RCLONE_CONFIG_PARODIA_PROVIDER=Other \
  RCLONE_CONFIG_PARODIA_ACCESS_KEY_ID="$AWS_ACCESS_KEY_ID" \
  RCLONE_CONFIG_PARODIA_SECRET_ACCESS_KEY="$AWS_SECRET_ACCESS_KEY" \
  RCLONE_CONFIG_PARODIA_ENDPOINT="$S3_ENDPOINT" RCLONE_CONFIG_PARODIA_REGION="$LOC"
rclone lsf parodia:$S3_BUCKET/host/ --dirs-only          # pick a <stamp>/

# DB: stream object -> age -d (with offline identity) -> pg_restore INSIDE the container
rclone cat parodia:$S3_BUCKET/host/<stamp>/authentik.sql.age \
  | age -d -i /path/to/offline-identity.txt \
  | docker exec -i authentik-postgresql-1 pg_restore -U authentik -d authentik --clean --if-exists
# (same shape for lemmy/akkoma; shared-* restore into `shared-postgres` as -U root -d <db>)

# Secrets: decrypt + extract (absolute paths; -P)
rclone cat parodia:$S3_BUCKET/host/<stamp>/secrets.tar.gz.age \
  | age -d -i /path/to/offline-identity.txt | sudo tar xzf - -P
```

---

## 6. Restore runbook A — hardware/disk failure (Hetzner snapshot)

The expected path. RTO ~15–30 min once the image is selected; RPO ≤ 24 h (last nightly snapshot).

1. **Confirm scope.** Hetzner auto-migrates a VM off failed hardware in many cases — check
   the [Hetzner Cloud Console](https://console.hetzner.cloud/) / status page first. Only do a
   backup-restore if the disk/data is actually lost.
2. **Restore the backup** (Console → server → *Backups* → pick the latest healthy slot →
   *Restore*, or `hcloud server restore <id> --image <backup-id>`).
   - Restoring **into the same server** keeps the **IP** → no DNS change needed.
   - If you must build a **new** server from the backup image, it gets a **new IP** → do §9
     DNS step. New server must be **≥** the original size.
3. **Boot & sanity:** SSH in, `docker ps` — Compose services with `restart: unless-stopped`
   come back on boot. Bring up any stopped stack manually (`cd <dir> && docker compose up -d`),
   infra order from §2 (traefik + shared-postgres first).
4. **Fix the pgdata-ownership gotcha** if Akkoma DB won't start:
   `sudo chown -R 70:70 /mash/akkoma/pgdata && sudo chmod 700 /mash/akkoma/pgdata && docker compose -f /mash/akkoma/docker-compose.yml restart db`.
5. **DNS** — only if the IP changed (§9).
6. **Verify** every service (§10).

## 7. Restore runbook B — rebuild on a fresh box (no usable snapshot)

Worst case: snapshots unusable / region loss / migrating providers. This is where the §5
offsite layer is load-bearing — without it, recovery from this state is **not possible**.

1. **Provision** a Debian 13 Hetzner VM ≥ original spec; install Docker + Compose; create
   users (`reala` sudoer; `mash`, `joseph`(uid 1000), `veola`, `pentarou` as needed for the
   owned trees).
2. **Restore directory trees** from the offsite bundle: `/mash/*`, `/opt/*`, `~/matrix`.
   Re-fix ownership: `/mash` → `mash:mash`; `/mash/akkoma` → `1000:1000` *then* `pgdata` →
   `70:70`; Authentik `data/media/public` → `1000:1000`.
3. **Restore databases** into fresh Postgres containers (start each empty, then):
   - `gunzip -c authentik.sql.gz | docker exec -i authentik-postgresql-1 psql -U authentik authentik`
   - same pattern for `lemmy`, `akkoma`, and each shared-postgres DB.
   - Akkoma DB must be **C collation** like the others; create the DB before restore.
4. **Re-apply Akkoma source patches** (memory: `endpoint.ex` RewriteOn first-plug,
   `config.exs` loginMethod=token, `registrations_open: false`) and rebuild
   (`mix deps.get && mix compile`), since a clean checkout loses tracked-file edits.
5. **Traefik:** restore `acme.json` (mode 600) or let ACME re-issue (watch LE rate limits).
6. **DNS** (§9 below) — point everything at the new IP.
7. **Verify** (§10).

## 8. Restore runbook C — a single service fell over

Most incidents are *one* service, not the whole box. Triage by failure type and escalate only
as far as you need to. The DB/secret restores (C2/C3) pull from the §5 offsite layer and need
the **offline age identity** — first rebuild the rclone env from the §5 *"Restoring from this
layer"* snippet, then pick a `<stamp>` with `rclone lsf parodia:$S3_BUCKET/host/ --dirs-only`.

**Service quick reference**

| Service | Container(s) | Compose dir | DB restore target |
|---|---|---|---|
| Authentik | `authentik-{server,worker,postgresql,redis}-1` | `/mash/authentik` | `authentik-postgresql-1` · `-U authentik -d authentik` |
| Akkoma | `akkoma-akkoma-1`, `akkoma-db-1` | `/mash/akkoma` | `akkoma-db-1` · `-U akkoma -d akkoma` |
| Lemmy | `lemmy-{lemmy,lemmy-ui,postgres,pictrs,lemmy-nginx}-1` | `/mash/lemmy` | `lemmy-postgres-1` · `-U lemmy -d lemmy` |
| Gitea | `gitea` | `/opt/gitea` | `shared-postgres` · `-U root -d gitea` |
| Miniflux | `miniflux` | `/opt/miniflux` | `shared-postgres` · `-U root -d miniflux` |
| WriteFreely | `writefreely` | `/opt/writefreely` | data in `mash-writefreely-data` vol (Hetzner snapshot only) |
| traefik / exim / uptime-kuma | as named | `/mash/traefik`, `/opt/exim-relay`, `/opt/uptime-kuma` | — (stateless or Hetzner only) |

> `/mash/*` dirs are `700 mash` — prefix compose commands there with `sudo`.

### C1 — Container down / crashed, data intact → restart (try this first)

```bash
docker ps -a --filter name=<svc>            # Exited / Restarting?
docker logs --tail=100 <container>          # why it died
cd <compose-dir> && docker compose up -d    # bring the stack back
docker compose restart <svc>                # or just bounce one service
```
- **Akkoma DB won't start** after any host-level `chown`: pgdata must be uid 70 →
  `sudo chown -R 70:70 /mash/akkoma/pgdata && sudo chmod 700 /mash/akkoma/pgdata && sudo docker compose -f /mash/akkoma/docker-compose.yml restart db`.
- **Akkoma app** runs from source, so a restart recompiles; if a bad `git reset` broke it,
  re-apply the tracked-file patches (`endpoint.ex` RewriteOn, `config.exs` loginMethod) first.
- **Bad auto-update?** Watchtower auto-pulls `latest`. Roll back by pinning the previous image
  tag in the compose file, then `docker compose up -d`.

### C2 — DB corrupted / bad migration / accidental delete → restore just that DB

Restore the one service's database from a chosen offsite dump. **Stop the app first** so nothing
writes mid-restore; `--clean --if-exists` drops & recreates objects in place.

```bash
# Authentik example — quiesce app, restore DB, bring it back:
cd /mash/authentik && sudo docker compose stop server worker
rclone cat parodia:$S3_BUCKET/host/<stamp>/authentik.sql.age \
  | age -d -i /path/to/offline-identity.txt \
  | docker exec -i authentik-postgresql-1 pg_restore -U authentik -d authentik --clean --if-exists
sudo docker compose start server worker
```
Same pattern per service (stop app → restore object → start app):
- **Lemmy** — `lemmy.sql.age` → `docker exec -i lemmy-postgres-1 pg_restore -U lemmy -d lemmy --clean --if-exists` (stop `lemmy lemmy-ui`)
- **Akkoma** — `akkoma.sql.age` → `docker exec -i akkoma-db-1 pg_restore -U akkoma -d akkoma --clean --if-exists` (stop `akkoma`)
- **Gitea** — `shared-gitea.sql.age` → `docker exec -i shared-postgres pg_restore -U root -d gitea --clean --if-exists` (stop `gitea`)
- **Miniflux** — `shared-miniflux.sql.age` → `docker exec -i shared-postgres pg_restore -U root -d miniflux --clean --if-exists` (stop `miniflux`)

### C3 — Lost / garbled config or secret → restore from the secrets bundle

Pull just the affected path out of the encrypted tarball (it stores absolute paths, so `-P`):

```bash
# list contents first (-t), then extract the specific path (-x):
rclone cat parodia:$S3_BUCKET/host/<stamp>/secrets.tar.gz.age | age -d -i /path/to/offline-identity.txt | tar tzf - -P | less
rclone cat parodia:$S3_BUCKET/host/<stamp>/secrets.tar.gz.age | age -d -i /path/to/offline-identity.txt | sudo tar xzf - -P  <path/to/restore>
```
- Authentik **blueprints** re-apply when `authentik-worker-1` (re)starts.
- **Re-fix ownership** after extracting into `/mash`: tree → `mash:mash`; `/mash/akkoma` →
  `1000:1000` *then* `pgdata` → `70:70`; Authentik `data/media/public` → `1000:1000`.

After any C-path fix, run the relevant rows of the §10 checklist for that service.

## 9. DNS / IP cutover (EuroDNS) — when the IP changes

Records are at **EuroDNS**, not on the box. Whenever the restore yields a new IP:

- Update **A** (and AAAA) for: `parodia.dev` apex, `www`, `matrix` *(after cutover)*,
  `authentik`, `social`, `lemmy`, `reader`, `git`, and any other app host that points here.
- **Keep TTL low (300 s)** during a recovery window so corrections propagate fast.
- Leave the well-known delegation (`m.server: matrix.parodia.dev:443`) intact — it's served
  by the box, so it follows the A record.
- Note (today): apex + `matrix` still point at **etke** (`46.225.142.216`) until the Matrix
  cutover; everything else already points at this box.

## 10. Post-restore verification checklist

- [ ] `docker ps` — all expected containers `Up`/healthy (compare to §2)
- [ ] **Traefik** serving valid TLS (not the default self-signed) on the app hosts
- [ ] **Authentik** login works → then OIDC into one downstream app (Miniflux/Lemmy) — proves
      the SSO chain, client secrets, and blueprints all survived
- [ ] Authentik: custom blueprints `status=successful`; app icons render (200); test email
      `docker exec authentik-worker-1 ak test_email <addr>`
- [ ] **Akkoma** `curl -s https://social.parodia.dev/api/v1/instance | grep version`; SSO button present; federation peers > 0
- [ ] **Lemmy** UI loads, login works, subscribed communities still federate
- [ ] **Gitea**, **WriteFreely**, **Miniflux**, Ditto/pete/pastel — each reachable + login
- [ ] **uptime-kuma** dashboard green across the board
- [ ] Outbound email leaves via exim-relay
- [ ] *(After Matrix cutover)* signing-key fingerprint matches
      (`/_matrix/key/v2/server`); existing user logs in **without** re-auth;
      federation send + receive both work

## 11. Pre-cutover DR readiness gate (do before Matrix lands)

The point of this doc. Don't bring Synapse over until:

- [x] Hetzner Backups confirmed **enabled**, freshest slot < 24 h old — verified 2026-06-28 (§12)
- [~] §5 offsite layer **built + running** (daily timer; all 6 objects upload, age-encrypt, and
      `pg_restore -l`-validate). **Remaining:** one real decrypt→restore with the offline age
      identity — an untested *restore* is still a hope, not a backup
- [ ] EuroDNS zone exported and committed
- [ ] This runbook **rehearsed once** end-to-end on a throwaway VM from the offsite bundle
- [ ] Hetzner console recovery/2FA stored offline
- [ ] age backup **identity** (private key — shared with the Ditto engine) stored offline in
      ≥2 places; losing it makes every encrypted S3 backup unrecoverable by design

## 12. Verify Hetzner backup state (re-runnable)

A read-only hcloud API token lives at `/etc/parodia/hcloud-ro.token` (0600 `reala`, sourced
from Ditto's `[observability].hetzner_token`). Confirm backups are still running and fresh —
do this as part of the §10 gate and after any scare:

```bash
TOKEN=$(cat /etc/parodia/hcloud-ro.token)
printf 'header = "Authorization: Bearer %s"\n' "$TOKEN" \
  | curl -sK- "https://api.hetzner.cloud/v1/servers?name=parodia-24gb-nbg1-1" \
  | python3 -c 'import json,sys;s=json.load(sys.stdin)["servers"][0];print("backups:", "ENABLED" if s["backup_window"] else "DISABLED", s["backup_window"])'
# list backup images + ages:
printf 'header = "Authorization: Bearer %s"\n' "$TOKEN" \
  | curl -sK- "https://api.hetzner.cloud/v1/images?type=backup&sort=created:desc"
```

(`printf` is a shell builtin, so the token never appears in `ps`/argv.) Last verified
2026-06-28: enabled, 7/7 daily slots, freshest ~8 h old.

> **Restore note:** the server has **no attached volumes** — the whole VM (incl. all
> `/mash` + `/opt` data) is one root disk, so a backup-image restore (runbook A) brings back
> *everything* in one shot, and runbook B must rebuild the entire tree from the S3 bundle
> (there's no separate data volume to reattach).
