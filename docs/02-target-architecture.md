# 02 — Target architecture (parodia.dev)

Hand-rolled Docker Compose. Reverse proxy is **pluggable**: ride the existing
`mash-traefik` at first, swap to our own proxy later without touching Synapse.

## Components (core phase)

| Service | Image | Notes |
|---|---|---|
| Synapse | `matrixdotorg/synapse` (pin to ≥ etke's version) | Homeserver. Client API on 8008, federation on 8448. |
| Postgres | `postgres:16-alpine` | **Dedicated** to Synapse, not shared with mash/lemmy/akkoma DBs. `C` collation. |
| Element Web | `vectorim/element-web` | Client at e.g. `chat.parodia.dev` (optional in core phase). |
| well-known | static files via proxy | `parodia.dev/.well-known/matrix/{server,client}`. No app needed — serve JSON. |

Deferred (later phases): MAS, Draupnir, bridges/bots, coturn/TURN.

## Hostnames / delegation

| Name | Serves |
|---|---|
| `parodia.dev/.well-known/matrix/server` | `{"m.server":"matrix.parodia.dev:8448"}` |
| `parodia.dev/.well-known/matrix/client` | homeserver base_url (clean — no etke/ketesa/vector.im) |
| `matrix.parodia.dev` (443) | Synapse client + federation API |
| `matrix.parodia.dev:8448` | Synapse federation listener (or 443 + well-known) |
| `chat.parodia.dev` | Element Web (optional) |
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
- Match major version to etke's dump (see `03-etke-export.md`); restore with `pg_restore`.

## Secrets / config carried from etke

`homeserver.signing.key`, `macaroon_secret_key`, `form_secret`,
`registration_shared_secret` — identical values, so existing sessions/tokens survive.
Everything else in `homeserver.yaml` is authored clean here.

## Versioning rule (why we don't need etke's exact versions)

- **Synapse:** only constraint is *new ≥ old* (schema migrates forward-only; never import
  into an older Synapse). `latest` satisfies this automatically — no need to match etke's version.
- **Postgres:** we import via logical `pg_dump`/`pg_restore`, not a data-dir copy, so a
  major-version mismatch is fine as long as *new ≥ old*. `postgres:16`/`17` both work.

So the stack can be built now with current images. etke's versions only matter as a
*floor* (don't go below them), which latest images already clear.

## Open questions (don't block the build)

- [ ] Ride mash-traefik, or stand up our own proxy now?
- [ ] Element Web in core phase, or clients keep pointing at `matrix.parodia.dev` directly?
- [ ] Federation: dedicated `:8448` listener, or 443-only via well-known?
