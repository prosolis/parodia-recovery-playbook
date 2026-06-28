# 01 — Source inventory (etke.cc)

What the etke-managed homeserver currently runs, and what we must pull off it.
Fill in the `?` rows during export — some need SSH/admin access to the etke box.

## Known from public recon (2026-06-20)

| Item | Value |
|---|---|
| `server_name` (identity) | `parodia.dev` |
| Homeserver host (delegation) | `matrix.parodia.dev` |
| Federation port | `8448` (via `.well-known/matrix/server`) |
| Client base_url | `https://matrix.parodia.dev` |
| Identity server | `vector.im` (legacy — drop) |
| MAS / MSC3861 | **Not enabled** |
| Known appservices | Draupnir (`@draupnir:parodia.dev`) |
| etke control hooks | `cc.etke.ketesa` well-known, `scheduler.ctrl.etke.cc` |

## Confirmed via SSH recon (2026-06-28) — `root@matrix.parodia.dev`

Box: dedicated Hetzner host, IP **46.225.142.216** (separate from the main parodia.dev
services host `178.104.56.222`). Ubuntu 24.04, 4 vCPU / 7.6G RAM / 150G disk (9% used).
etke/mash layout under `/matrix`, systemd units + `vars.yml`, `registry.etke.cc` images.

| Item | Confirmed value | Migration impact |
|---|---|---|
| Synapse version | **v1.155.0** (monolith) | floor only — `latest` ≥ this |
| **Postgres version** | **18.4** | ⚠️ target must be **PG ≥ 18** — old plan said 16 (can't restore an 18 dump into 16). Main host's shared-postgres is already 18-alpine. |
| Local users | **31** on `parodia.dev` | accounts to preserve |
| DB size | **2.9 G** (`/matrix/postgres`) | small — fast dump/restore |
| Media store | **1.3 G** (`/matrix/synapse/storage/media-store`, ~1.3G local_content) | small — rsync is quick |
| `max_upload_size` | **1024M** | carry into clean config |
| `default_room_version` | **'12'** | recent — keep; needs current Synapse |
| `enable_authenticated_media` | **true** (MSC3916) | keep on (default in current Synapse) |
| Registration | `enable_registration: true` **but** `registration_requires_token: true` | token-gated today; becomes MAS/Authentik later |
| Appservices | **only** `appservice-double-puppet` (vestigial mautrix helper, no bridges) | drop — no bridges in scope |
| Draupnir | runs as a **bot user** `@draupnir:parodia.dev` (not an appservice) | deferred (not selected for carry-over) |
| Email | etke wired `email:` → `matrix-exim-relay` (`enable_notifs: true`) | **drop** — Authentik owns auth/notifs |
| TURN / coturn | `turn_shared_secret` present; coturn realm `turn.matrix.parodia.dev`, `external-ip=46.225.142.216`, ports 49152–49252, `use-auth-secret` | **carry calls** — new coturn, regen secret, `external-ip` → 178.104.56.222 |
| etke lock-in | `matrix-ketesa`, `container-socket-proxy`, `scheduler.ctrl.etke.cc` callback, `cc.etke.ketesa` well-known | **drop all** |
| Client web | etke Element at `element.parodia.dev` (`email.client_base_url`) | carry Element Web (hostname `element.parodia.dev`) |

## Critical artifacts to export (do NOT regenerate)

1. **Signing key** — `homeserver.signing.key`. Losing this breaks federation + device trust permanently.
2. **Postgres database** — full `pg_dump` of the Synapse DB.
3. **Media store** — `media-store/` (local_content + remote_content + thumbnails).
4. Appservice registration files (`*.registration.yaml`) — needed if/when we re-add bridges/bots.
5. Any `macaroon_secret_key` / `form_secret` / `registration_shared_secret` from `homeserver.yaml` — keep identical so existing sessions/tokens survive.

> See `03-etke-export.md` for the actual export procedure.
