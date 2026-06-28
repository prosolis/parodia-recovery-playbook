# 05 — Synapse workers (performance)

Synapse's main process is largely single-threaded (Python GIL). On a 12-core box that
means one busy core while 11 idle. **Workers** split Synapse into multiple processes that
parallelize the hot paths (sync, federation, media) across cores, coordinated over Redis.

## Sequencing — do this SECOND, after the monolith cutover

Workers are added **after** the etke→monolith cutover is verified green (see `04`). Reasons:

- The monolith→workers conversion is **non-destructive and reversible** — same Postgres,
  same data, same signing key. You can roll back by pointing Traefik at the monolith again.
- Isolates failure domains: if federation breaks during the *migration*, you don't want to
  also be debugging worker routing at the same time.

So: Phase 1 = monolith (`docker-compose.yml`). Phase 2 = this overlay
(`docker-compose.workers.yml`), applied once Phase 1 is stable.

## Box sizing (parodia.dev, confirmed 2026-06-20)

- 12 cores (AMD EPYC Genoa), load ~0.2 (idle). ~12 GiB free. **No swap** → don't over-commit.
- Budget: ~6 workers + main ≈ 3–4 GiB light load; parallelizes onto ~8 cores under pressure,
  leaving 4 cores + RAM margin for lemmy/akkoma/authentik/etc.

## Topology

| Instance | `worker_app` | Handles |
|---|---|---|
| `sync1`, `sync2` | `generic_worker` | client `/sync`, `/client` read endpoints |
| `fedreader` | `generic_worker` | inbound `/_matrix/federation/*`, `/_matrix/key/*` |
| `fedsender` | `synapse.app.generic_worker` (sender role) | outbound federation |
| `mediaworker` | `synapse.app.generic_worker` (media role) | media up/download, thumbnails |
| `bg` | `generic_worker` | background tasks + pushers + appservice notify |
| `main` | (homeserver) | startup, replication hub, leftover endpoints |

**Held for later** (only if load demands; more fragile): event persister + other stream
writers (typing/receipts/to-device/account-data), sharded federation senders, a 3rd sync.

## Required supporting pieces

1. **Redis** — worker replication bus. Dedicated container (`redis`/`valkey`), internal network only.
2. **instance_map** — main's replication HTTP listener (`synapse:9093`) so workers find it.
3. **nginx worker-router** — Synapse's path→worker routing is dozens of regexes; the documented
   approach is an internal nginx in front of Synapse. Traefik routes `matrix.parodia.dev` → this
   nginx → workers/main. (Encoding it as Traefik labels is impractical.)
4. **Postgres** — each worker has its own pool. Bumped `max_connections` + tuned buffers
   (done in base `docker-compose.yml`, benefits the monolith too).

## Main config flags (set when overlay is active)

```yaml
redis:
  enabled: true
  host: redis
instance_map:
  main:
    host: synapse
    port: 9093
# offload single-purpose roles off main:
send_federation: false
federation_sender_instances: [fedsender]
start_pushers: false
pusher_instances: [bg]
notify_appservices_from_worker: bg
run_background_tasks_on: bg
media_instance_running_background_jobs: mediaworker
```

## Validation on first apply (can't be tested pre-deploy)

- [ ] `docker compose -f docker-compose.yml -f docker-compose.workers.yml up -d`
- [ ] each worker logs "Synapse now listening" + connects to Redis replication
- [ ] `/sync` served by sync workers (check nginx access log / `X-Synapse` timing)
- [ ] federation send + receive still work (federation tester)
- [ ] media up/download works
- [ ] Postgres connection count stays under `max_connections` (`SELECT count(*) FROM pg_stat_activity;`)

## Rollback

Point Traefik back at the monolith service and `docker compose stop` the workers + nginx.
Data is untouched (same DB) — it's a routing change, not a data change.
