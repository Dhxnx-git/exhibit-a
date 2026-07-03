"""Canonical poster module — invoked as `python -m exhibit_a.action_post`.

This is the ONLY component that holds a GitHub token, and it is deliberately
tiny and dumb: read a receipt file, upload it as an issue comment, exit. It
runs the model NEVER and executes repo code NEVER, so attacker-controlled
issue text has no path to the one privileged step in the whole system.

See action/post_receipt.py and .github/workflows/exhibit-repro.yml for the
two-job split this is the safe half of.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

MAX_COMMENT = 60_000  # GitHub caps comments at 65536; leave headroom.


def post_comment(repo: str, issue_number: str, token: str, body: str) -> int:
    url = f"https://api.github.com/repos/{repo}/issues/{issue_number}/comments"
    payload = json.dumps({"body": body[:MAX_COMMENT]}).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload, method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "exhibit-a-bot",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return 0 if 200 <= resp.status < 300 else 1
    except urllib.error.HTTPError as e:
        print(f"github api error {e.code}: {e.read().decode('utf-8', 'replace')[:400]}",
              file=sys.stderr)
        return 1
    except urllib.error.URLError as e:
        print(f"network error posting comment: {e}", file=sys.stderr)
        return 1


def main() -> int:
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    issue = os.environ.get("EXHIBIT_ISSUE_NUMBER", "")
    token = os.environ.get("GITHUB_TOKEN", "")
    receipt_path = os.environ.get("EXHIBIT_RECEIPT", "receipt.md")

    if not (repo and issue and token):
        print("missing GITHUB_REPOSITORY / EXHIBIT_ISSUE_NUMBER / GITHUB_TOKEN",
              file=sys.stderr)
        return 2
    try:
        with open(receipt_path, encoding="utf-8") as f:
            body = f.read()
    except OSError as e:
        body = (f"exhibit-a could not produce a receipt for this issue "
                f"(the reproduction job did not emit `{receipt_path}`).")
        print(f"receipt read failed: {e}", file=sys.stderr)
    return post_comment(repo, issue, token, body)


if __name__ == "__main__":
    raise SystemExit(main())
