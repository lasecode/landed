"""Check the WhatsApp token: send Meta's pre-approved hello_world template to WA_PING (a phone number)."""
import os

from django.core.management.base import BaseCommand

from accounts.models import normalize_phone
from messaging.whatsapp import client


class Command(BaseCommand):
    def handle(self, **kw):
        to = normalize_phone(os.getenv("WA_PING", ""))
        if not to:
            return self.stdout.write("WA_PING skipped: no number")
        try:
            mid = client._post({"messaging_product": "whatsapp", "to": to, "type": "template",
                                "template": {"name": "hello_world", "language": {"code": "en_US"}}})
            self.stdout.write(f"WA_PING PASS sent hello_world to ..{to[-4:]} id={mid}")
        except Exception as e:  # noqa: BLE001
            self.stdout.write(f"WA_PING FAIL {type(e).__name__}: {str(e)[:300]}")
