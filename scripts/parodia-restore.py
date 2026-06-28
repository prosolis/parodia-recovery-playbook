#!/usr/bin/env python3
"""
parodia-restore — interactive disaster-recovery driver for the parodia.dev host.

One tool that replaces the copy-paste recipes in docs/06-restoration-playbook.md.
You give it the S3 key pair (to read the offsite backups), your OFFLINE age
identity (to decrypt them), and — only if you want a whole-VM rollback — a
write-capable Hetzner API token. It then:

  * discovers the available backup stamps in s3://<bucket>/host/<stamp>/,
  * shows their MANIFEST so you can see what each one holds,
  * restores a single service's database, or one secret/config path, or the
    whole secrets bundle, in place, with the app quiesced around the restore,
  * lists the Hetzner whole-VM backup images and (with a write token + a typed
    confirmation) triggers a rebuild-from-backup of the entire server.

Design: pure python3 stdlib. It shells out to the SAME tools the backup layer
uses — rclone, age, docker — instead of reimplementing S3/age in Python, so a
fresh disaster box needs no `pip install`. The Hetzner API is plain urllib.

Key custody: this script never holds the age private key on the box. You point
`--identity` at the offline identity file; decryption happens locally and the
plaintext is streamed straight into pg_restore / tar (never written to disk).

Usage:
    sudo ./parodia-restore.py                      # interactive menu
    sudo ./parodia-restore.py --identity ~/age.key # preload the offline key
    ./parodia-restore.py --hcloud-only             # just the VM-backup view

Config is auto-discovered from /etc/parodia/{s3-backup.env,hcloud-ro.token};
override with --s3-env / --hcloud-token-file / env vars. See docs/06 §5.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import shlex
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

# ── service registry ─────────────────────────────────────────────────────────
# Mirrors docs/06 §8 "Service quick reference". Each service maps its S3 dump
# object to the live container/DB it restores into, the compose dir used to
# quiesce the app around the restore, and any post-restore fixups.
SHARED_PG = "shared-postgres"  # gitea + miniflux live in the shared cluster

SERVICES = {
    "authentik": {
        "object": "authentik.sql.age",
        "pg_container": "authentik-postgresql-1",
        "pg_user": "authentik",
        "pg_db": "authentik",
        "compose_dir": "/mash/authentik",
        "stop": ["server", "worker"],
        "note": "SSO chokepoint — restore + verify this before the OIDC apps.",
    },
    "lemmy": {
        "object": "lemmy.sql.age",
        "pg_container": "lemmy-postgres-1",
        "pg_user": "lemmy",
        "pg_db": "lemmy",
        "compose_dir": "/mash/lemmy",
        "stop": ["lemmy", "lemmy-ui"],
    },
    "akkoma": {
        "object": "akkoma.sql.age",
        "pg_container": "akkoma-db-1",
        "pg_user": "akkoma",
        "pg_db": "akkoma",
        "compose_dir": "/mash/akkoma",
        "stop": ["akkoma"],
        "post_restore_pgdata_uid": 70,  # re-chown pgdata after any host touch
    },
    "gitea": {
        "object": "shared-gitea.sql.age",
        "pg_container": SHARED_PG,
        "pg_user": "root",
        "pg_db": "gitea",
        "compose_dir": "/opt/gitea",
        "stop": ["gitea"],
    },
    "miniflux": {
        "object": "shared-miniflux.sql.age",
        "pg_container": SHARED_PG,
        "pg_user": "root",
        "pg_db": "miniflux",
        "compose_dir": "/opt/miniflux",
        "stop": ["miniflux"],
    },
}

DEFAULT_S3_ENV = "/etc/parodia/s3-backup.env"
DEFAULT_HCLOUD_TOKEN_FILE = "/etc/parodia/hcloud-ro.token"
DEFAULT_SERVER_ID = 123765145  # parodia-24gb-nbg1-1
HCLOUD_API = "https://api.hetzner.cloud/v1"
REMOTE = "parodia"  # rclone env-remote name (built below, like parodia-backup)


# ── small ui helpers ─────────────────────────────────────────────────────────
def c(code: str, s: str) -> str:
    return s if os.environ.get("NO_COLOR") else f"\033[{code}m{s}\033[0m"


def hdr(s: str) -> None:
    print("\n" + c("1;36", f"── {s} " + "─" * max(0, 60 - len(s))))


def info(s: str) -> None:
    print(c("36", "  " + s))


def ok(s: str) -> None:
    print(c("32", "  ✓ " + s))


def warn(s: str) -> None:
    print(c("33", "  ! " + s))


def err(s: str) -> None:
    print(c("31", "  ✗ " + s), file=sys.stderr)


def die(s: str) -> "None":
    err(s)
    sys.exit(1)


def ask(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    try:
        v = input(c("1", f"  {prompt}{suffix}: ")).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        die("aborted")
    return v or (default or "")


def confirm(prompt: str) -> bool:
    return ask(f"{prompt} (y/N)").lower() in ("y", "yes")


def menu(title: str, options: list[tuple[str, str]]) -> str:
    """options = [(key, label)]; returns the chosen key."""
    hdr(title)
    for key, label in options:
        print(f"   {c('1;33', key)})  {label}")
    keys = {k for k, _ in options}
    while True:
        choice = ask("choose")
        if choice in keys:
            return choice
        warn("pick one of: " + ", ".join(sorted(keys)))


# ── config / environment ─────────────────────────────────────────────────────
class Config:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.s3: dict[str, str] = {}
        self.identity: str | None = args.identity
        self.hcloud_token: str | None = None
        self.server_id: int = args.server_id
        self.rclone_env: dict[str, str] = {}

    # -- S3 / rclone --
    def load_s3(self) -> None:
        path = self.args.s3_env
        env: dict[str, str] = {}
        if path and os.path.isfile(path):
            for raw in _read_lines(path):
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
            info(f"loaded S3 config from {path}")
        # env vars / prompts fill any gaps
        for key, prompt, secret in (
            ("S3_ENDPOINT", "S3 endpoint (https://hel1.your-objectstorage.com)", False),
            ("S3_BUCKET", "S3 bucket", False),
            ("AWS_ACCESS_KEY_ID", "S3 access key id", False),
            ("AWS_SECRET_ACCESS_KEY", "S3 secret access key", True),
        ):
            val = env.get(key) or os.environ.get(key)
            if not val:
                val = (getpass.getpass(c("1", f"  {prompt}: ")) if secret
                       else ask(prompt))
            env[key] = val
        self.s3 = env
        # Hetzner Object Storage enforces region == endpoint location on writes,
        # and it's harmless on reads — derive it (hel1/fsn1/nbg1) like the backup.
        host = env["S3_ENDPOINT"].split("://", 1)[-1]
        location = host.split(".", 1)[0] or env.get("S3_REGION", "hel1")
        self.rclone_env = {
            "RCLONE_CONFIG": "/dev/null",
            f"RCLONE_CONFIG_{REMOTE.upper()}_TYPE": "s3",
            f"RCLONE_CONFIG_{REMOTE.upper()}_PROVIDER": "Other",
            f"RCLONE_CONFIG_{REMOTE.upper()}_ACCESS_KEY_ID": env["AWS_ACCESS_KEY_ID"],
            f"RCLONE_CONFIG_{REMOTE.upper()}_SECRET_ACCESS_KEY": env["AWS_SECRET_ACCESS_KEY"],
            f"RCLONE_CONFIG_{REMOTE.upper()}_ENDPOINT": env["S3_ENDPOINT"],
            f"RCLONE_CONFIG_{REMOTE.upper()}_REGION": location,
            f"RCLONE_CONFIG_{REMOTE.upper()}_ACL": "private",
        }

    @property
    def bucket(self) -> str:
        return self.s3["S3_BUCKET"]

    def rclone_environ(self) -> dict[str, str]:
        return {**os.environ, **self.rclone_env}

    # -- age identity --
    def need_identity(self) -> str:
        if self.identity and os.path.isfile(self.identity):
            return self.identity
        warn("an OFFLINE age identity (private key) is required to decrypt backups.")
        info("this is the key whose PUBLIC recipient the host backs up to; it never")
        info("lives on the box. Point to the file you keep offline.")
        while True:
            path = os.path.expanduser(ask("path to age identity file"))
            if os.path.isfile(path):
                self.identity = path
                return path
            warn(f"no such file: {path}")

    # -- hcloud token --
    def load_hcloud(self, *, write_hint: bool = False) -> str | None:
        if self.hcloud_token:
            return self.hcloud_token
        tok = None
        if self.args.hcloud_token:
            tok = self.args.hcloud_token
        elif os.environ.get("HCLOUD_TOKEN"):
            tok = os.environ["HCLOUD_TOKEN"]
        elif self.args.hcloud_token_file and os.path.isfile(self.args.hcloud_token_file):
            tok = _read_lines(self.args.hcloud_token_file)[0].strip() if _read_lines(self.args.hcloud_token_file) else None
            if tok:
                info(f"loaded Hetzner token from {self.args.hcloud_token_file}")
        if not tok:
            if write_hint:
                warn("a WRITE-capable Hetzner token is needed to trigger a rebuild.")
                info("the staged /etc/parodia/hcloud-ro.token is read-only by design.")
            tok = getpass.getpass(c("1", "  Hetzner Cloud API token (blank to skip): ")).strip()
        self.hcloud_token = tok or None
        return self.hcloud_token


def _read_lines(path: str) -> list[str]:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.readlines()


# ── tool preflight ───────────────────────────────────────────────────────────
def preflight(need: list[str]) -> None:
    missing = [t for t in need if shutil.which(t) is None]
    if missing:
        die("missing required tool(s): " + ", ".join(missing) +
            "\n    install them (e.g. apt install age rclone) and retry.")


# ── rclone helpers ───────────────────────────────────────────────────────────
def rclone_lsf(cfg: Config, path: str, dirs_only: bool = False) -> list[str]:
    cmd = ["rclone", "lsf", f"{REMOTE}:{cfg.bucket}/{path}"]
    if dirs_only:
        cmd.append("--dirs-only")
    res = subprocess.run(cmd, env=cfg.rclone_environ(),
                         capture_output=True, text=True)
    if res.returncode != 0:
        err("rclone lsf failed: " + res.stderr.strip())
        return []
    return [line.rstrip("/") for line in res.stdout.splitlines() if line.strip()]


def rclone_cat_text(cfg: Config, path: str) -> str | None:
    res = subprocess.run(["rclone", "cat", f"{REMOTE}:{cfg.bucket}/{path}"],
                         env=cfg.rclone_environ(), capture_output=True, text=True)
    if res.returncode != 0:
        return None
    return res.stdout


def list_stamps(cfg: Config) -> list[str]:
    stamps = sorted(rclone_lsf(cfg, "host/", dirs_only=True), reverse=True)
    return stamps


def pick_stamp(cfg: Config) -> str | None:
    stamps = list_stamps(cfg)
    if not stamps:
        err("no backup stamps found under host/ — check the S3 creds/bucket.")
        return None
    hdr("available backups (newest first)")
    for i, s in enumerate(stamps):
        tag = c("32", " (latest)") if i == 0 else ""
        print(f"   {c('1;33', str(i))})  {s}{tag}")
    raw = ask("pick a backup number", "0")
    try:
        idx = int(raw)
        return stamps[idx]
    except (ValueError, IndexError):
        warn("invalid selection")
        return None


def show_manifest(cfg: Config, stamp: str) -> None:
    text = rclone_cat_text(cfg, f"host/{stamp}/MANIFEST.txt")
    hdr(f"manifest — {stamp}")
    if text:
        print("\n".join("   " + ln for ln in text.splitlines()))
    else:
        objs = rclone_lsf(cfg, f"host/{stamp}/")
        info("no MANIFEST.txt; objects present:")
        for o in objs:
            print("    - " + o)


# ── streaming pipeline: rclone cat | age -d | <sink> ─────────────────────────
def decrypt_pipeline(cfg: Config, s3_path: str, sink_cmd: list[str],
                     sink_stdin_is_terminal_ok: bool = False) -> bool:
    """Stream an encrypted S3 object through age -d into sink_cmd's stdin.

    Plaintext is never written to disk — it flows rclone → age → sink in memory.
    Returns True on success of the whole chain.
    """
    identity = cfg.need_identity()
    src = ["rclone", "cat", f"{REMOTE}:{cfg.bucket}/{s3_path}"]
    dec = ["age", "-d", "-i", identity]
    info("stream: " + c("2", " | ".join(
        [" ".join(map(shlex.quote, src)), "age -d -i <identity>",
         " ".join(map(shlex.quote, sink_cmd))])))
    p_src = subprocess.Popen(src, stdout=subprocess.PIPE, env=cfg.rclone_environ())
    p_dec = subprocess.Popen(dec, stdin=p_src.stdout, stdout=subprocess.PIPE)
    p_src.stdout.close()  # let p_src receive SIGPIPE if p_dec exits
    p_sink = subprocess.Popen(sink_cmd, stdin=p_dec.stdout)
    p_dec.stdout.close()
    p_sink.communicate()
    rc_src = p_src.wait()
    rc_dec = p_dec.wait()
    rc_sink = p_sink.returncode
    if rc_src != 0:
        err(f"rclone cat exited {rc_src} (S3 read failed?)")
    if rc_dec != 0:
        err(f"age -d exited {rc_dec} (wrong identity, or not the matching key?)")
    if rc_sink != 0:
        err(f"sink ({sink_cmd[0]} …) exited {rc_sink}")
    return rc_src == 0 and rc_dec == 0 and rc_sink == 0


# ── docker / compose helpers ─────────────────────────────────────────────────
def compose(svc: dict, *args: str) -> list[str]:
    return ["sudo", "docker", "compose"] + list(args)


def compose_run(svc: dict, *args: str) -> int:
    cmd = compose(svc, *args)
    info("$ (cd %s) %s" % (svc["compose_dir"], " ".join(cmd)))
    return subprocess.run(cmd, cwd=svc["compose_dir"]).returncode


def container_exists(name: str) -> bool:
    res = subprocess.run(["docker", "ps", "-a", "--format", "{{.Names}}",
                          "--filter", f"name=^{name}$"],
                         capture_output=True, text=True)
    return name in res.stdout.split()


# ── restore actions ──────────────────────────────────────────────────────────
def restore_database(cfg: Config) -> None:
    names = list(SERVICES)
    choice = menu("restore a single service database",
                  [(str(i), n + (f"  — {SERVICES[n].get('note', '')}"
                                 if SERVICES[n].get("note") else ""))
                   for i, n in enumerate(names)] + [("q", "back")])
    if choice == "q":
        return
    svc_name = names[int(choice)]
    svc = SERVICES[svc_name]

    if not container_exists(svc["pg_container"]):
        warn(f"DB container {svc['pg_container']} not found running. The stack may "
             "need to be up first (compose up -d) so the empty DB exists.")
        if not confirm("continue anyway?"):
            return

    stamp = pick_stamp(cfg)
    if not stamp:
        return
    show_manifest(cfg, stamp)

    hdr(f"restore plan — {svc_name}")
    info(f"object   : host/{stamp}/{svc['object']}")
    info(f"into     : {svc['pg_container']}  (pg_restore -U {svc['pg_user']} "
         f"-d {svc['pg_db']} --clean --if-exists)")
    info(f"app stop : compose stop {' '.join(svc['stop'])}  (in {svc['compose_dir']})")
    warn("--clean --if-exists DROPs and recreates objects in place. The current DB "
         f"contents of '{svc['pg_db']}' will be replaced by this backup.")
    if not confirm(f"restore {svc_name} from {stamp}?"):
        info("cancelled")
        return

    # 1) quiesce the app so nothing writes mid-restore
    info("stopping app containers …")
    compose_run(svc, "stop", *svc["stop"])

    # 2) stream the dump into pg_restore inside the version-matched container
    sink = ["docker", "exec", "-i", svc["pg_container"],
            "pg_restore", "-U", svc["pg_user"], "-d", svc["pg_db"],
            "--clean", "--if-exists"]
    success = decrypt_pipeline(cfg, f"host/{stamp}/{svc['object']}", sink)

    # 3) akkoma pgdata must be uid 70 if a host-level chown ever touched it
    if svc.get("post_restore_pgdata_uid"):
        uid = svc["post_restore_pgdata_uid"]
        pgdata = os.path.join(svc["compose_dir"], "pgdata")
        info(f"re-fixing {pgdata} ownership to {uid}:{uid} …")
        subprocess.run(["sudo", "chown", "-R", f"{uid}:{uid}", pgdata])
        subprocess.run(["sudo", "chmod", "700", pgdata])

    # 4) bring the app back
    info("starting app containers …")
    compose_run(svc, "start", *svc["stop"])

    if success:
        ok(f"{svc_name} database restored from {stamp}.")
        if svc_name == "authentik":
            info("Authentik is the SSO chokepoint — verify a downstream OIDC login next.")
        elif svc_name == "akkoma":
            info("Akkoma runs from source; if the app misbehaves, re-apply the tracked-file "
                 "patches (endpoint.ex RewriteOn, config.exs loginMethod) and recompile.")
    else:
        err(f"{svc_name} restore did NOT complete cleanly — review the errors above "
            "before trusting the data.")


def restore_secret_path(cfg: Config) -> None:
    stamp = pick_stamp(cfg)
    if not stamp:
        return
    obj = f"host/{stamp}/secrets.tar.gz.age"

    # list the bundle contents (decrypt → tar -tz) so the user can pick a path
    hdr("listing the secrets bundle")
    if not decrypt_pipeline(cfg, obj, ["tar", "tzf", "-", "-P"]):
        err("could not list the bundle — wrong identity or missing object.")
        return

    target = ask("path to extract (blank = extract the WHOLE bundle)")
    hdr("extract plan")
    info(f"bundle : {obj}")
    if target:
        info(f"path   : {target}  (single path)")
    else:
        warn("you chose to extract the ENTIRE bundle — this overwrites every backed-up "
             "config/secret path in place. Usually you want a single path instead.")
    if not confirm("extract now?"):
        info("cancelled")
        return

    sink = ["sudo", "tar", "xzf", "-", "-P"]
    if target:
        sink.append(target.lstrip("/") if not target.startswith("/") else target)
    success = decrypt_pipeline(cfg, obj, sink)
    if success:
        ok("extracted.")
        warn("re-fix ownership where needed: /mash tree → mash:mash; /mash/akkoma → "
             "1000:1000 then pgdata → 70:70; Authentik data/media/public → 1000:1000.")
        info("Authentik blueprints re-apply when authentik-worker-1 (re)starts.")
    else:
        err("extraction failed — see errors above.")


# ── Hetzner whole-VM backups ─────────────────────────────────────────────────
def hcloud_api(cfg: Config, method: str, path: str,
               body: dict | None = None) -> dict:
    token = cfg.hcloud_token
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        HCLOUD_API + path, data=data, method=method,
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read() or "{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        raise RuntimeError(f"Hetzner API {method} {path} → {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Hetzner API unreachable: {e.reason}") from e


def hcloud_view(cfg: Config) -> list[dict]:
    if not cfg.load_hcloud():
        warn("no Hetzner token provided — skipping the VM-backup view.")
        return []
    try:
        srv = hcloud_api(cfg, "GET",
                         f"/servers/{cfg.server_id}")["server"]
    except RuntimeError as e:
        err(str(e))
        return []
    hdr(f"Hetzner server {srv['name']} (id {srv['id']})")
    window = srv.get("backup_window")
    info(f"status        : {srv.get('status')}")
    info(f"public IP     : {srv.get('public_net', {}).get('ipv4', {}).get('ip')}")
    info(f"backups       : " + (c("32", f"ENABLED (window {window} UTC)") if window
                                else c("31", "DISABLED")))
    try:
        images = hcloud_api(
            cfg, "GET",
            f"/images?type=backup&bound_to={cfg.server_id}&sort=created:desc"
        )["images"]
    except RuntimeError as e:
        err(str(e))
        return []
    hdr("whole-VM backup images (newest first)")
    if not images:
        warn("no backup images bound to this server.")
        return []
    for i, im in enumerate(images):
        size = im.get("image_size") or im.get("disk_size")
        print(f"   {c('1;33', str(i))})  id={im['id']}  created={im['created']}  "
              f"size={size}GB  desc={im.get('description') or '-'}")
    return images


def hcloud_rebuild(cfg: Config) -> None:
    images = hcloud_view(cfg)
    hdr("rebuild server from a whole-VM backup")
    warn("THIS WIPES THE SERVER and replaces its disk with the chosen backup image.")
    warn("Everything written since that backup is lost. The IP is preserved.")
    if not images:
        info("no images to rebuild from.")
        return
    # ensure we have a write-capable token (the staged one is read-only)
    info("a write-capable token is required for the rebuild action.")
    if confirm("provide a different (write) token now?"):
        cfg.hcloud_token = None
        cfg.args.hcloud_token = None
        cfg.args.hcloud_token_file = None
        cfg.load_hcloud(write_hint=True)
        if not cfg.hcloud_token:
            info("no token — cancelled.")
            return

    raw = ask("image number to rebuild from (blank = cancel)")
    if not raw:
        return
    try:
        image = images[int(raw)]
    except (ValueError, IndexError):
        warn("invalid selection")
        return

    try:
        srv = hcloud_api(cfg, "GET", f"/servers/{cfg.server_id}")["server"]
    except RuntimeError as e:
        err(str(e))
        return
    name = srv["name"]
    hdr("FINAL CONFIRMATION")
    warn(f"about to rebuild server '{name}' (id {cfg.server_id}) from backup "
         f"image {image['id']} ({image['created']}).")
    warn("This is destructive and irreversible. To proceed, type the server name exactly.")
    if ask(f"type '{name}' to confirm") != name:
        info("name did not match — cancelled.")
        return
    try:
        res = hcloud_api(cfg, "POST", f"/servers/{cfg.server_id}/actions/rebuild",
                         {"image": image["id"]})
    except RuntimeError as e:
        die(str(e))
    action = res.get("action", {})
    ok(f"rebuild action started: id={action.get('id')} status={action.get('status')}")
    rootpw = res.get("root_password")
    if rootpw:
        warn(f"new root password (shown ONCE): {rootpw}")
    info("watch progress in the Hetzner console. After it boots: SSH in, verify "
         "docker ps, then run the §10 verification checklist (and §9 DNS if the IP changed).")


# ── top-level menu ───────────────────────────────────────────────────────────
def interactive(cfg: Config) -> None:
    print(c("1;35", "\n  parodia.dev — restoration driver"))
    print(c("2", "  docs/06-restoration-playbook.md is the narrative; this is the lever.\n"))
    cfg.load_s3()
    while True:
        choice = menu("what do you want to do?", [
            ("1", "List / inspect available offsite backups (S3)"),
            ("2", "Restore ONE service database  (Runbook C2)"),
            ("3", "Restore ONE config/secret path  (Runbook C3)"),
            ("4", "View Hetzner whole-VM backups  (Runbook A — situational)"),
            ("5", "REBUILD the whole server from a Hetzner backup  (Runbook A — destructive)"),
            ("q", "Quit"),
        ])
        try:
            if choice == "1":
                stamp = pick_stamp(cfg)
                if stamp:
                    show_manifest(cfg, stamp)
            elif choice == "2":
                preflight(["rclone", "age", "docker"])
                restore_database(cfg)
            elif choice == "3":
                preflight(["rclone", "age", "tar"])
                restore_secret_path(cfg)
            elif choice == "4":
                hcloud_view(cfg)
            elif choice == "5":
                hcloud_rebuild(cfg)
            elif choice == "q":
                print()
                return
        except KeyboardInterrupt:
            print()
            warn("interrupted — back to menu")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Interactive disaster-recovery driver for parodia.dev "
                    "(see docs/06-restoration-playbook.md).")
    ap.add_argument("--s3-env", default=DEFAULT_S3_ENV,
                    help=f"file with S3_ENDPOINT/S3_BUCKET/AWS_* (default {DEFAULT_S3_ENV})")
    ap.add_argument("--identity",
                    help="path to the OFFLINE age identity (private key) for decryption")
    ap.add_argument("--hcloud-token", help="Hetzner Cloud API token (else file/env/prompt)")
    ap.add_argument("--hcloud-token-file", default=DEFAULT_HCLOUD_TOKEN_FILE,
                    help=f"file holding the Hetzner token (default {DEFAULT_HCLOUD_TOKEN_FILE})")
    ap.add_argument("--server-id", type=int, default=DEFAULT_SERVER_ID,
                    help=f"Hetzner server id (default {DEFAULT_SERVER_ID})")
    ap.add_argument("--hcloud-only", action="store_true",
                    help="skip S3 setup; only show/operate the Hetzner VM backups")
    args = ap.parse_args()

    cfg = Config(args)
    if args.hcloud_only:
        cfg.load_hcloud()
        hcloud_view(cfg)
        return
    interactive(cfg)


if __name__ == "__main__":
    main()
