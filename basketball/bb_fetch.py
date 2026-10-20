"""CollegeBasketballData.com: the men's basketball data source.

The same author and the same key system as collegefootballdata.com, and,
their site says, the same quota: "API use shares a football/basketball
quota". So every call here is counted in the ledger the football fetcher
keeps (tiebreaker/data/.api-local.log) and that tools/api-budget.py reads,
and every response is kept.

KEPT, NOT REFETCHED. Each response is written to data/raw/ under a name made
from its endpoint and parameters before anything parses it, so a parser bug
costs a re-parse and never a second call. A caller that wants fresh data
asks for it with fresh=True; everything else reads the copy on disk.
"""
import datetime
import json
import os
import re
import subprocess
import sys
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(HERE, "data")
RAW = os.path.join(DATA, "raw")
API = "https://api.collegebasketballdata.com"

# The football fetcher's ledger, on purpose: one quota, one ledger.
USAGE_LOG = os.path.join(ROOT, "tiebreaker", "data", ".api-local.log")


def key():
    """The key, from the environment or the football section's .env, which
    is the same key: CBBD and CFBD issue one between them."""
    k = os.environ.get("CBBD_API_KEY") or os.environ.get("CFBD_API_KEY")
    if not k:
        env = os.path.join(ROOT, "tiebreaker", ".env")
        if os.path.exists(env):
            for line in open(env):
                if line.startswith(("CBBD_API_KEY=", "CFBD_API_KEY=")):
                    k = line.split("=", 1)[1].strip().strip("'\"")
                    break
    if not k:
        sys.exit("No CBBD_API_KEY or CFBD_API_KEY in the environment or "
                 "tiebreaker/.env.")
    return k


def _note_call(path):
    """Record one call. Never raises: a ledger is not worth a failed fetch."""
    try:
        stamp = datetime.datetime.now(datetime.timezone.utc).isoformat(
            timespec="seconds")
        with open(USAGE_LOG, "a", encoding="utf-8") as f:
            f.write(f"{stamp} cbbd/{path.split('?')[0].strip('/')}\n")
    except OSError:
        pass


def raw_path(path, params):
    """Where a response is kept: the endpoint and its parameters, sorted, so
    the same question always lands in the same file."""
    slug = re.sub(r"[^A-Za-z0-9]+", "-", path.strip("/")).strip("-")
    q = "_".join(f"{k}-{re.sub(r'[^A-Za-z0-9]+', '', str(v))}"
                 for k, v in sorted(params.items()))
    return os.path.join(RAW, f"{slug}{'__' + q if q else ''}.json")


def get(path, params=None, fresh=False):
    """One GET, kept on disk. curl rather than urllib so the key never
    lands in a URL log.

    Without `fresh`, a response already on disk is returned and nothing is
    asked. With it, the call is made, counted and kept, and only a parsed
    success replaces the kept copy: a refusal or an error page never
    overwrites good data."""
    params = params or {}
    p = raw_path(path, params)
    if not fresh and os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    qs = urllib.parse.urlencode(params)
    _note_call(path)
    r = subprocess.run(
        ["curl", "-sS", "-m", "90", "-H", f"Authorization: Bearer {key()}",
         "-H", "Accept: application/json", f"{API}/{path.strip('/')}"
         + (f"?{qs}" if qs else "")],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"curl failed on {path}: {r.stderr[:150]}")
    try:
        data = json.loads(r.stdout)
    except ValueError:
        raise RuntimeError(f"CBBD returned something that is not JSON for "
                           f"{path}: {r.stdout[:150]!r}")
    if isinstance(data, dict) and ("message" in data or "error" in data):
        raise RuntimeError(f"CBBD refused {path}: "
                           f"{data.get('message') or data.get('error')}")
    os.makedirs(RAW, exist_ok=True)
    # Compact, one game to a line: a season of Division I games is about
    # 6,000 rows, and indented it was three times the size for nothing a
    # reader needs. One row per line still diffs cleanly.
    with open(p, "w", encoding="utf-8") as f:
        if isinstance(data, list):
            f.write("[\n" + ",\n".join(
                json.dumps(row, sort_keys=True, separators=(",", ":"))
                for row in data) + "\n]\n")
        else:
            json.dump(data, f, sort_keys=True, separators=(",", ":"))
    return data
