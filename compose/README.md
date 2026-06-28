# compose/ — the hand-rolled Matrix stack

Synapse + dedicated Postgres + Element-less core + static well-known, behind the
existing `mash-traefik` (swap-able). `server_name=parodia.dev` preserved.

## Defaults chosen (each a one-file change)

- **Reverse proxy:** rides `mash-traefik` via labels in `docker-compose.yml`. To use your
  own proxy later, edit the labels + the `proxy` network — nothing else moves.
- **Federation: 443-only** via well-known (`well-known/server` → `matrix.parodia.dev:443`),
  avoiding a separate `:8448` cert. etke currently advertises `:8448`; we flip the
  well-known at cutover. (Remote servers cache well-known briefly — expected.)

## First run

```bash
cp .env.example .env                          # fill POSTGRES_PASSWORD, PROXY_NETWORK, CERT_RESOLVER
cp synapse/homeserver.yaml.example synapse/homeserver.yaml
# put the etke signing key here, copy the 3 secrets into homeserver.yaml:
#   synapse/homeserver.signing.key
#   macaroon_secret_key / form_secret / registration_shared_secret
```

## Migration bring-up (don't boot Synapse on an empty DB — see docs/04)

```bash
./import-from-etke.sh /path/to/synapse.dump /path/to/media-store/
# imports DB + media into a clean C-collation Postgres, then tells you to:
docker compose up -d synapse
docker compose logs -f synapse    # watch forward schema migrations on first boot
```

## Smoke test before flipping DNS

```bash
# point matrix.parodia.dev at this host locally first (/etc/hosts), then:
curl https://matrix.parodia.dev/_matrix/client/versions
curl https://matrix.parodia.dev/_matrix/key/v2/server   # signing key fingerprint should match etke
```

## Not here yet (later phases)

- Element Web client service
- MAS + Authentik OIDC (`auth.parodia.dev`)
- Draupnir + any other appservices from the etke export
- coturn/TURN for VoIP
