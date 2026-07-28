import argparse
import json
import os
import logging
from base64 import b64encode
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)


def _load_env_file(path):
    if not path.exists():
        return

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env_file(Path(__file__).resolve().parents[1] / ".env")

ARGYLE_BASE_URL = os.getenv("ARGYLE_BASE_URL", "https://api-sandbox.argyle.com/v2")
ARGYLE_API_KEY = os.getenv("ARGYLE_SANDBOX_API_ID")
ARGYLE_API_SECRET = os.getenv("ARGYLE_SANDBOX_API_SECRET")


def _argyle_headers():
    if not ARGYLE_API_KEY or not ARGYLE_API_SECRET:
        raise RuntimeError(
            "Set ARGYLE_SANDBOX_API_KEY and ARGYLE_SANDBOX_PROD_KEY before running."
        )

    credentials = f"{ARGYLE_API_KEY}:{ARGYLE_API_SECRET}".encode("utf-8")
    encoded_credentials = b64encode(credentials).decode("ascii")
    return {
        "Authorization": f"Basic {encoded_credentials}",
        "Accept": "application/json",
    }


def _get_json(url):
    request = Request(url, headers=_argyle_headers(), method="GET")
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def list_all_users(limit=200, external_id=None):
    params = {"limit": limit}
    if external_id:
        params["external_id"] = external_id

    next_url = f"{ARGYLE_BASE_URL}/users?{urlencode(params)}"
    users = []

    while next_url:
        page = _get_json(next_url)
        users.extend(page.get("results", []))
        next_url = page.get("next")

    return users


def main():
    parser = argparse.ArgumentParser(description="List all Argyle users.")
    parser.add_argument("--limit", type=int, default=200, help="Results per page, max 200.")
    parser.add_argument("--external-id", help="Filter users by exact external_id.")
    args = parser.parse_args()

    try:
        users = list_all_users(limit=args.limit, external_id=args.external_id)
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise SystemExit(f"Argyle request failed: HTTP {error.code}\n{body}") from error
    except URLError as error:
        raise SystemExit(f"Argyle request failed: {error.reason}") from error

    print(json.dumps(users, indent=2))


if __name__ == "__main__":
    main()
