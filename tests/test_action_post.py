"""The poster is the one privileged component, so its contract is worth
pinning: it posts to the right URL with the token, caps oversized bodies, and
reports API failures instead of swallowing them. We stub urlopen — no network,
no real token, no real repo."""

import io
import json

import exhibit_a.action_post as ap


class _Resp:
    def __init__(self, status): self.status = status
    def __enter__(self): return self
    def __exit__(self, *a): return False


def test_posts_to_issue_comments_endpoint(monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout=0):
        seen["url"] = req.full_url
        seen["auth"] = req.headers.get("Authorization")
        seen["body"] = json.loads(req.data.decode("utf-8"))["body"]
        return _Resp(201)

    monkeypatch.setattr(ap.urllib.request, "urlopen", fake_urlopen)
    rc = ap.post_comment("owner/repo", "42", "tok_abc", "hello receipt")
    assert rc == 0
    assert seen["url"].endswith("/repos/owner/repo/issues/42/comments")
    assert seen["auth"] == "Bearer tok_abc"
    assert seen["body"] == "hello receipt"


def test_oversized_body_is_capped(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=0):
        captured["len"] = len(json.loads(req.data.decode("utf-8"))["body"])
        return _Resp(201)

    monkeypatch.setattr(ap.urllib.request, "urlopen", fake_urlopen)
    ap.post_comment("o/r", "1", "t", "x" * 200_000)
    assert captured["len"] <= ap.MAX_COMMENT


def test_http_error_is_reported_not_swallowed(monkeypatch):
    def boom(req, timeout=0):
        raise ap.urllib.error.HTTPError(req.full_url, 403, "forbidden", {}, io.BytesIO(b"nope"))

    monkeypatch.setattr(ap.urllib.request, "urlopen", boom)
    assert ap.post_comment("o/r", "1", "t", "body") == 1


def test_main_requires_env(monkeypatch):
    for var in ("GITHUB_REPOSITORY", "EXHIBIT_ISSUE_NUMBER", "GITHUB_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    assert ap.main() == 2
