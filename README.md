# Matrix Migration: etke.cc → self-hosted on parodia.dev

Migrating the `parodia.dev` Matrix homeserver off **etke.cc** managed hosting onto a
**hand-rolled Docker Compose** stack on the parodia.dev host, while **preserving the
existing federation identity** (`server_name`, signing key, user IDs, room history).

## Constraints & decisions

| Decision | Choice | Why |
|---|---|---|
| Deployment tooling | Hand-rolled `docker-compose` (no Ansible) | We own the wiring; converting the rest of the host off Ansible/mash anyway |
| Federation identity | **Preserve** `server_name=parodia.dev` + signing key | Keep all `@user:parodia.dev` IDs, history, federation, device trust |
| Auth | Move to **MAS + Authentik OIDC** (new) | Not on MAS today; Authentik becomes central auth |
| Migration order | **Core first**, then bridges/bots | Lowest-risk staged cutover |
| Reverse proxy | Pluggable (mash-traefik now, own proxy later) | mash-traefik is Ansible-managed and may be replaced |

## Current source state (etke.cc) — from recon

- `server_name`: **`parodia.dev`** (MXIDs are `@user:parodia.dev`)
- Delegation: `.well-known/matrix/server` → `matrix.parodia.dev:8448`; client base_url `https://matrix.parodia.dev`
- **Not on MAS** (no MSC3861 / `m.authentication`; `externalAuthProvider: false`)
- Appservices in play: at least **Draupnir** (`@draupnir:parodia.dev`) — deferred to a later phase
- etke artifacts to drop: `cc.etke.ketesa` well-known block, `scheduler.ctrl.etke.cc` admin hook, `m.identity_server: vector.im`

## Target architecture (parodia.dev)

```
                 ┌─────────────────────────── reverse proxy (mash-traefik → own later) ──┐
  parodia.dev/.well-known/matrix/*   →  static well-known (server_name delegation)
  matrix.parodia.dev (443)           →  Synapse client/federation API
  matrix.parodia.dev:8448            →  Synapse federation listener
  (later) auth.parodia.dev           →  MAS  ──OIDC──>  Authentik (already running)
                 └───────────────────────────────────────────────────────────────────────┘
                          │
        ┌─────────────────┼───────────────────┐
     Synapse          Postgres (Synapse)    Element Web
   (homeserver)      (dedicated, not shared)  (client)
```

## Repo layout

```
docs/
  01-source-inventory.md     what etke currently runs; what to export
  02-target-architecture.md  the compose stack + networking/proxy plan
  03-etke-export.md          step-by-step data export (signing key, DB, media)
  04-cutover-runbook.md      DNS/delegation cutover, downtime window, verify, rollback
  05-workers.md              workered Synapse topology (Phase-2 overlay)
  06-restoration-playbook.md whole-host disaster-recovery / restoration runbook
compose/                     the hand-rolled Synapse + Postgres + well-known stack
scripts/recon-etke.sh        etke.cc recon helper
```

## Status

Scaffolding phase. Next: export inventory from etke, then build `compose/`.
See `docs/` for the working plan.
