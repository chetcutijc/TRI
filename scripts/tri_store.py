"""
Keep TRI's private files in a private Supabase Storage bucket instead of this public repo.

  python scripts/tri_store.py pull   # bucket data/  -> local data/   (start of a workflow run)
  python scripts/tri_store.py push   # local data/   -> bucket data/  (end of a run)
                                     # local docs/   -> bucket site/  (the built dashboard)

Bucket layout ("tri", private):
  data/*.json, data/coaching_email.txt   sync state the scripts read and write
  site/index.html, site/print.html, ...  the built dashboard, shown by the sign-in page

The first run after switching over finds the bucket empty and seeds it from the
repo's frozen data/ folder. After that the bucket is the source of truth.

Env (GitHub secrets, same ones push_health.py uses):
  WT_SUPABASE_URL, WT_SUPABASE_SERVICE_ROLE_KEY
"""
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BUCKET = "tri"
DATA_DIR = Path("data")
SITE_DIR = Path("docs")          # where build_dashboard.py writes (never published now)
REPO_OWNED = {"manual_log.json"}  # still edited on GitHub; never synced to the bucket
SITE_FILES = ["index.html", "print.html", "dashboard.pdf", "ai_coaching_adjustments.ics"]

URL = os.environ.get("WT_SUPABASE_URL", "").rstrip("/")
KEY = os.environ.get("WT_SUPABASE_SERVICE_ROLE_KEY", "")


def _req(method, path, body=None, headers=None, raw=False):
    # New secret keys (sb_secret_...) aren't JWTs and go only in the apikey
    # header; the legacy service_role JWT also goes in Authorization.
    h = {"apikey": KEY}
    if not KEY.startswith("sb_"):
        h["Authorization"] = f"Bearer {KEY}"
    h.update(headers or {})
    req = urllib.request.Request(f"{URL}/storage/v1/{path}", data=body, method=method, headers=h)
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
        return data if raw else (json.loads(data) if data else None)


def list_objects(prefix):
    body = json.dumps({"prefix": prefix, "limit": 1000, "offset": 0}).encode()
    rows = _req("POST", f"object/list/{BUCKET}", body, {"Content-Type": "application/json"})
    # Folders come back with id = null; we only want files
    return [r["name"] for r in rows or [] if r.get("id")]


def download(name):
    return _req("GET", f"object/authenticated/{BUCKET}/{urllib.parse.quote(name)}", raw=True)


def upload(name, content, content_type):
    _req("POST", f"object/{BUCKET}/{urllib.parse.quote(name)}", content, {
        "Content-Type": content_type,
        "x-upsert": "true",
        "cache-control": "max-age=0",
    })


def pull():
    names = list_objects("data")
    if not names:
        print("Bucket has no data yet: seeding from the repo's data/ folder on push.")
        return
    DATA_DIR.mkdir(exist_ok=True)
    for name in names:
        if name in REPO_OWNED:
            continue
        (DATA_DIR / name).write_bytes(download(f"data/{name}"))
        print(f"  pulled data/{name}")
    print(f"Pulled {len(names)} file(s) from the private bucket.")


def push():
    count = 0
    for f in sorted(DATA_DIR.glob("*")):
        if f.is_file() and f.name not in REPO_OWNED:
            ctype = "application/json" if f.suffix == ".json" else "text/plain; charset=utf-8"
            upload(f"data/{f.name}", f.read_bytes(), ctype)
            count += 1
    for name in SITE_FILES:
        f = SITE_DIR / name
        if f.exists():
            ctype = {".html": "text/html; charset=utf-8", ".ics": "text/calendar"}.get(
                f.suffix, mimetypes.guess_type(name)[0] or "application/octet-stream")
            upload(f"site/{name}", f.read_bytes(), ctype)
            count += 1
    print(f"Pushed {count} file(s) to the private bucket.")


if __name__ == "__main__":
    if not URL or not KEY:
        # Fail loudly: running without the bucket would either lose state (pull)
        # or silently stop updating the dashboard (push).
        sys.exit("WT_SUPABASE_URL / WT_SUPABASE_SERVICE_ROLE_KEY are not set.")
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        {"pull": pull, "push": push}[cmd]()
    except KeyError:
        sys.exit("usage: tri_store.py pull|push")
    except urllib.error.HTTPError as e:
        sys.exit(f"Storage error {e.code}: {e.read().decode(errors='replace')[:300]}")
