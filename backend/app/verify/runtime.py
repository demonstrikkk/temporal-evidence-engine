"""One store per process, chosen explicitly.

The verifier writes from a worker thread while the API reads from request
handlers, so they must share an instance. Two `LocalStore` objects would each
hold their own in-memory copy and overwrite each other's `state.json`; the
Postgres adapter could tolerate it, but pretending one adapter needs a
singleton and the other does not is exactly the kind of quiet difference that
breaks in a demo.
"""
from __future__ import annotations

from functools import lru_cache

from app.verify.store import VerifyStore, build_store


@lru_cache(maxsize=1)
def get_store() -> VerifyStore:
    return build_store()


def reset_store() -> None:
    """Test seam: forget the cached adapter so a temporary directory is used."""
    get_store.cache_clear()
