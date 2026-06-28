# 02 — Target architecture (parodia.dev)

Hand-rolled Docker Compose. Reverse proxy is **pluggable**: ride the existing
`mash-traefik` at first, swap to our own proxy later without touching Synapse.

## Components (core phase)

| Service | Image | Notes |
|---|---|---|
| Synapse | `ghcr.io/element-hq/synapse:latest` (≥ etke's v1.155.0) | Homeserver. Client + federation on 8008, 443-only via well-known. |
| Postgres | `postgres:18-alpine` | **Dedicated** to Synapse, not shared with mash/lemmy/akkoma DBs. `C` collation. Must be **≥ 18** (etke source is PG 18 — logical restore can't downgrade). |
| Element Web | `vectorim/element-web` | Client at **`element.parodia.dev`** (the hostname etke served it on). |
| coturn | `coturn/coturn` | TURN relay for voice/video calls. Host networking; shares `turn_shared_secret` with Synapse. |
| well-known | static files via proxy | `parodia.dev/.well-known/matrix/{server,client}`. No app needed — serve JSON. |

Deferred (later phases): MAS + Authentik OIDC, Draupnir, bridges/bots.
Dropped from etke entirely: email/SMTP (Authentik owns auth + notifs), ketesa,
container-socket-proxy, the `appservice-double-puppet` helper (no bridges).

## Hostnames / delegation

| Name | Serves |
|---|---|
| `parodia.dev/.well-known/matrix/server` | `{"m.server":"matrix.parodia.dev:8448"}` |
| `parodia.dev/.well-known/matrix/client` | homeserver base_url (clean — no etke/ketesa/vector.im) |
| `matrix.parodia.dev` (443) | Synapse client + federation API |
| `matrix.parodia.dev:8448` | Synapse federation listener (or 443 + well-known) |
| `element.parodia.dev` | Element Web |
| `matrix.parodia.dev:3478/5349` | coturn TURN (calls); relay range 49152–49252/udp |
| `auth.parodia.dev` (later) | MAS → Authentik OIDC |

`server_name` stays **`parodia.dev`** — never changes, or all MXIDs break.

## Networking

- Synapse + its Postgres on a private compose network; only Synapse's HTTP port is
  exposed to the proxy network.
- To ride `mash-traefik`: attach Synapse to the proxy's docker network + add Traefik
  labels (or a Traefik file-provider entry). Keep these labels in **one** override file
  so swapping proxies later is a single-file change.
- Postgres is **never** published to the host/internet.

## Postgres notes (migration-critical)

- Synapse requires `LC_COLLATE=C` and `LC_CTYPE=C` on its DB — initialize the new
  Postgres accordingly before `pg_restore`, or restore will create a subtly broken DB.
- Target major must be **≥ the source major**. etke runs **PG 18** (confirmed 2026-06-28),
  so the new Postgres is pinned `18-alpine`. A logical dump from PG 18 will NOT restore into
  17/16 — that's a downgrade `pg_restore` refuses. (The host's shared-postgres is also 18.)

## Secrets / config carried from etke

`homeserver.signing.key`, `macaroon_secret_key`, `form_secret`,
`registration_shared_secret` — identical values, so existing sessions/tokens survive.
Everything else in `homeserver.yaml` is authored clean here.

## Versioning rule (why we don't need etke's exact versions)

- **Synapse:** only constraint is *new ≥ old* (schema migrates forward-only; never import
  into an older Synapse). `latest` satisfies this automatically — no need to match etke's version.
- **Postgres:** we import via logical `pg_dump`/`pg_restore`, not a data-dir copy, so the
  target major must be *≥ source*. etke is PG 18 → pin **`postgres:18`** (NOT 16/17 — those
  can't restore an 18 dump).

So the stack can be built now with current images. etke's versions only matter as a
*floor* (don't go below them) — Synapse `latest` clears v1.155.0; Postgres must be 18.

## Resolved (confirmed 2026-06-28)

- **Proxy:** ride `mash-traefik` now (network `traefik`, entrypoint `web-secure`, certResolver `default`).
- **Element:** in the core phase, at `element.parodia.dev`.
- **Federation:** 443-only via well-known (`m.server: matrix.parodia.dev:443`).
- **Calls:** coturn in the core phase (user wants VoIP).
- **Email:** dropped (Authentik/MAS owns auth + notifications).
