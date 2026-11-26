import os
import sys

from django.apps import AppConfig


class DashboardConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "dashboard"

    def ready(self):
        """EMBED_SCHEDULER=true runs the periodic jobs inside the web process (single-instance hosting only)."""
        from django.conf import settings

        if not settings.EMBED_SCHEDULER or "pytest" in sys.modules or os.environ.get("LANDED_SCHEDULER_STARTED"):
            return
        if not any(cmd in " ".join(sys.argv) for cmd in ("gunicorn", "runserver")):
            return
        os.environ["LANDED_SCHEDULER_STARTED"] = "1"
        from apscheduler.schedulers.background import BackgroundScheduler

        from payments import tasks

        s = BackgroundScheduler(timezone="Africa/Lagos")
        s.add_job(tasks.expire_invoices, "interval", minutes=15, id="expire")
        s.add_job(tasks.reconcile, "interval", hours=3, id="reconcile")
        s.add_job(tasks.send_reminders, "interval", hours=1, id="reminders")
        s.add_job(tasks.sync_payouts, "interval", minutes=30, id="payouts")
        s.start()
