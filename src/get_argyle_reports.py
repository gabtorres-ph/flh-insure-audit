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
            "Set ARGYLE_SANDBOX_API_ID and ARGYLE_SANDBOX_API_SECRET before running."
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


def _list_all(endpoint, limit=200, **filters):
    params = {"limit": limit}
    params.update({key: value for key, value in filters.items() if value is not None})

    next_url = f"{ARGYLE_BASE_URL}/{endpoint}?{urlencode(params)}"
    results = []

    while next_url:
        page = _get_json(next_url)
        results.extend(page.get("results", []))
        next_url = page.get("next")

    return results


def list_all_users(limit=200, external_id=None):
    return _list_all("users", limit=limit, external_id=external_id)


def list_all_shifts(
    limit=200,
    account=None,
    user=None,
    from_start_datetime=None,
    to_start_datetime=None,
):
    return _list_all(
        "shifts",
        limit=limit,
        account=account,
        user=user,
        from_start_datetime=from_start_datetime,
        to_start_datetime=to_start_datetime,
    )


def list_all_gigs(
    limit=200,
    account=None,
    user=None,
    from_start_datetime=None,
    to_start_datetime=None,
):
    return _list_all(
        "gigs",
        limit=limit,
        account=account,
        user=user,
        from_start_datetime=from_start_datetime,
        to_start_datetime=to_start_datetime,
    )


def list_all_vehicles(limit=200, account=None, user=None):
    return _list_all("vehicles", limit=limit, account=account, user=user)


def main():
    parser = argparse.ArgumentParser(description="List Argyle resources.")
    parser.add_argument(
        "resource",
        nargs="?",
        default="users",
        choices=("users", "shifts", "gigs", "vehicles"),
        help="Argyle resource to list. Defaults to users.",
    )
    parser.add_argument("--limit", type=int, default=200, help="Results per page, max 200.")
    parser.add_argument("--external-id", help="Filter users by exact external_id.")
    parser.add_argument("--account", help="Filter shifts, gigs, or vehicles by account ID.")
    parser.add_argument("--user", help="Filter shifts, gigs, or vehicles by user ID.")
    parser.add_argument(
        "--from-start-datetime",
        help="Filter shifts or gigs with start_datetime on or after this ISO 8601 timestamp.",
    )
    parser.add_argument(
        "--to-start-datetime",
        help="Filter shifts or gigs with start_datetime on or before this ISO 8601 timestamp.",
    )
    args = parser.parse_args()

    try:
        if args.resource == "users":
            results = list_all_users(limit=args.limit, external_id=args.external_id)
        elif args.resource == "shifts":
            results = list_all_shifts(
                limit=args.limit,
                account=args.account,
                user=args.user,
                from_start_datetime=args.from_start_datetime,
                to_start_datetime=args.to_start_datetime,
            )
        elif args.resource == "gigs":
            results = list_all_gigs(
                limit=args.limit,
                account=args.account,
                user=args.user,
                from_start_datetime=args.from_start_datetime,
                to_start_datetime=args.to_start_datetime,
            )
        else:
            results = list_all_vehicles(
                limit=args.limit,
                account=args.account,
                user=args.user,
            )
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise SystemExit(f"Argyle request failed: HTTP {error.code}\n{body}") from error
    except URLError as error:
        raise SystemExit(f"Argyle request failed: {error.reason}") from error

    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
