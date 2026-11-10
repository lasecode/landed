"""Background job dispatch.

With REDIS_URL set, jobs go to RQ (run `python -m app.worker`). Without it (local dev, tests),
jobs run inline right away so everything still works end to end.
"""
from __future__ import annotations

import importlib
import logging

from .config import settings

log = logging.getLogger("landed.tasks")
_queue = None


def _get_queue():
    global _queue
    if _queue is None:
        from redis import Redis
        from rq import Queue

        _queue = Queue("landed", connection=Redis.from_url(settings.redis_url))
    return _queue


def _resolve(path: str):
    mod, fn = path.rsplit(".", 1)
    return getattr(importlib.import_module(mod), fn)


def enqueue(path: str, *args, **kwargs) -> None:
    """path like 'app.jobs.process_webhook_event'."""
    if settings.redis_url:
        _get_queue().enqueue(path, *args, retry=_retry(), **kwargs)
        return
    try:
        _resolve(path)(*args, **kwargs)
    except Exception:  # inline mode: log, don't break the caller
        log.exception("inline job %s failed", path)


def _retry():
    from rq import Retry

    return Retry(max=4, interval=[10, 60, 300, 900])
