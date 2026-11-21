from django.db import models
from django.utils import timezone

from accounts.models import Business


class Notification(models.Model):
    """Every WhatsApp message we send (or log in dry-run), with its delivery status."""
    business = models.ForeignKey(Business, null=True, blank=True, on_delete=models.CASCADE, related_name="notifications")
    to = models.CharField(max_length=20)
    template = models.CharField(max_length=60)  # template name, or "text" for free-form replies
    params = models.JSONField(default=list, blank=True)
    body = models.TextField(blank=True, help_text="Rendered text, for the record")
    invoice = models.ForeignKey("payments.Invoice", null=True, blank=True, on_delete=models.SET_NULL)
    status = models.CharField(max_length=12, default="queued")  # queued|sent|logged|delivered|read|failed|skipped
    error = models.TextField(blank=True)
    wa_message_id = models.CharField(max_length=120, blank=True, db_index=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]


class WhatsAppContact(models.Model):
    """Last inbound message per number, for WhatsApp's 24-hour free-form window."""
    phone = models.CharField(max_length=20, primary_key=True)
    last_inbound_at = models.DateTimeField(default=timezone.now)


class BotSession(models.Model):
    """Where a seller is in a menu conversation (like a USSD session). Expires after a few minutes idle."""
    phone = models.CharField(max_length=20, primary_key=True)
    state = models.CharField(max_length=40, default="menu")
    data = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(default=timezone.now)
