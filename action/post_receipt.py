"""Thin shim kept for discoverability.

The real, installed poster lives in the package at exhibit_a/action_post.py so
that `python -m exhibit_a.action_post` works after `pip install exhibit-a-bot`.
This file just re-exports it, so anyone who goes looking in action/ lands in
the right place.
"""

from exhibit_a.action_post import main, post_comment  # noqa: F401

if __name__ == "__main__":
    raise SystemExit(main())
