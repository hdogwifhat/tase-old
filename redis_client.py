"""
redis_client.py — Redis connection and helpers for TASE Screener.

Uses REDIS_URL env var. If not set, all operations are no-ops
(returns None on get, silently skips on set) so the app degrades
gracefully to file-based fallbacks.
"""

import os, json

_client = None
_disabled = False  # set True if Redis is not configured or unreachable

REDIS_URL = os.getenv('REDIS_URL', '')


def _get_client():
    global _client, _disabled
    if _disabled:
        return None
    if _client is not None:
        return _client
    if not REDIS_URL:
        _disabled = True
        return None
    try:
        import redis
        _client = redis.from_url(
            REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
        )
        _client.ping()
        print(f'[Redis] Connected to {REDIS_URL[:30]}...', flush=True)
        return _client
    except Exception as e:
        print(f'[Redis] Could not connect ({e}). Running without Redis.', flush=True)
        _disabled = True
        return None


def is_available():
    return _get_client() is not None


def rget(key):
    r = _get_client()
    if r is None:
        return None
    try:
        v = r.get(key)
        return json.loads(v) if v else None
    except Exception as e:
        print(f'[Redis] rget {key}: {e}', flush=True)
        return None


def rset(key, val, ttl=None):
    r = _get_client()
    if r is None:
        return
    try:
        v = json.dumps(val, default=str)
        if ttl:
            r.setex(key, int(ttl), v)
        else:
            r.set(key, v)
    except Exception as e:
        print(f'[Redis] rset {key}: {e}', flush=True)


def rdel(*keys):
    r = _get_client()
    if r is None:
        return
    try:
        r.delete(*keys)
    except Exception:
        pass


def rttl(key):
    r = _get_client()
    if r is None:
        return -2
    try:
        return r.ttl(key)
    except Exception:
        return -2


def acquire_lock(name, ttl=120):
    """Distributed lock using SET NX EX. Returns True if lock acquired."""
    r = _get_client()
    if r is None:
        return True  # no Redis → always allow (single process anyway)
    try:
        return bool(r.set(f'lock:{name}', '1', nx=True, ex=int(ttl)))
    except Exception:
        return True


def release_lock(name):
    rdel(f'lock:{name}')
