"""Run the background worker:  python -m app.worker"""
from __future__ import annotations

import logging

from redis import Redis
from rq import Queue, Worker

from .config import settings

logging.basicConfig(level=logging.INFO)

if __name__ == "__main__":
    if not settings.redis_url:
        raise SystemExit("REDIS_URL is not set")
    conn = Redis.from_url(settings.redis_url)
    Worker([Queue("landed", connection=conn)], connection=conn).work(with_scheduler=False)
