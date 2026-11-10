"""Periodic jobs:  python -m app.scheduler

- every 15 min: expire unpaid invoices whose account has expired
- every N hours: reconciliation against each provider's transaction list (catches missed webhooks)
- daily 10:00 Lagos: payment reminders on each merchant's schedule
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.blocking import BlockingScheduler

from .config import settings
from .tasks import enqueue

logging.basicConfig(level=logging.INFO)


def build(background: bool = False):
    s = (BackgroundScheduler if background else BlockingScheduler)(timezone="Africa/Lagos")
    s.add_job(lambda: enqueue("app.jobs.expire_invoices"), "interval", minutes=15, id="expire")
    s.add_job(lambda: enqueue("app.jobs.reconcile_all"), "interval", hours=settings.reconcile_every_hours, id="reconcile")
    s.add_job(lambda: enqueue("app.jobs.send_reminders"), "cron", hour=10, minute=0, id="reminders")
    return s


def main() -> None:
    build().start()


if __name__ == "__main__":
    main()
