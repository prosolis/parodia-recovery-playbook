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

## To confirm during export (needs etke access)

- [ ] Synapse version (so the new image matches; never downgrade a DB)
- [ ] Postgres major version (pg_dump/restore must be compatible)
- [ ] Full appservice list (`/matrix/synapse/config/*.yaml` registrations): Draupnir + any bridges/bots (baibot? honoroit? hookshot? mautrix-*?)
- [ ] Media store size (drives transfer time + downtime window)
- [ ] DB size
- [ ] Any custom `homeserver.yaml` overrides etke added (rate limits, federation allow/deny, retention, URL previews)
- [ ] TURN/coturn config (VoIP) — host/secret
- [ ] Registration settings (open/closed, token, email verification)
- [ ] Email/SMTP relay in use (etke uses exim; parodia already has `mash-exim-relay`)

## Critical artifacts to export (do NOT regenerate)

1. **Signing key** — `homeserver.signing.key`. Losing this breaks federation + device trust permanently.
2. **Postgres database** — full `pg_dump` of the Synapse DB.
3. **Media store** — `media-store/` (local_content + remote_content + thumbnails).
4. Appservice registration files (`*.registration.yaml`) — needed if/when we re-add bridges/bots.
5. Any `macaroon_secret_key` / `form_secret` / `registration_shared_secret` from `homeserver.yaml` — keep identical so existing sessions/tokens survive.

> See `03-etke-export.md` for the actual export procedure.
