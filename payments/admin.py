"""Landed's own back office (Django admin at /admin/)."""
from django.contrib import admin, messages

from .models import Customer, Invoice, LedgerEntry, Payment, ReconciliationRun, VirtualAccount, WebhookEvent
from .money import naira


class LedgerInline(admin.TabularInline):
    model = LedgerEntry
    fields = ("created_at", "type", "amount", "note")
    readonly_fields = fields
    extra = 0
    can_delete = False


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("ref", "business", "customer", "amount_ngn", "paid_ngn", "status", "created_at")
    list_filter = ("status", "excess_status")
    search_fields = ("number", "customer__name", "business__name", "description")
    raw_id_fields = ("business", "customer", "created_by")
    readonly_fields = ("amount_paid", "public_token", "paid_at")
    inlines = [LedgerInline]

    @admin.display(description="Amount")
    def amount_ngn(self, o):
        return naira(o.amount)

    @admin.display(description="Paid")
    def paid_ngn(self, o):
        return naira(o.amount_paid)


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("reference", "business", "amount_ngn", "payer_name", "status", "invoice", "source", "paid_at")
    list_filter = ("status", "source", "channel", "provider")
    search_fields = ("reference", "payer_name", "account_number", "business__name")
    raw_id_fields = ("business", "account", "invoice", "customer")
    readonly_fields = ("reference", "amount", "fees", "paid_at", "provider", "channel")

    @admin.display(description="Amount")
    def amount_ngn(self, o):
        return naira(o.amount)


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("name", "business", "phone", "credit_balance", "created_at")
    search_fields = ("name", "phone", "business__name")
    raw_id_fields = ("business",)


@admin.register(VirtualAccount)
class VirtualAccountAdmin(admin.ModelAdmin):
    list_display = ("account_number", "bank_name", "kind", "business", "customer", "invoice", "active", "expires_at")
    list_filter = ("kind", "active")
    search_fields = ("account_number", "provider_reference", "business__name")
    raw_id_fields = ("business", "customer", "invoice")


@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):
    list_display = ("received_at", "provider", "event_type", "reference", "status", "signature_valid", "detail")
    list_filter = ("status", "provider", "signature_valid")
    search_fields = ("reference",)
    readonly_fields = [f.name for f in WebhookEvent._meta.fields]
    actions = ["replay"]

    @admin.action(description="Re-process (verify with Paystack again)")
    def replay(self, request, qs):
        from .tasks import process_webhook

        n = 0
        for ev in qs.filter(signature_valid=True):
            ev.status = "queued"
            ev.save(update_fields=["status"])
            process_webhook.delay(ev.id)
            n += 1
        self.message_user(request, f"Queued {n} event(s).", messages.SUCCESS)


@admin.register(ReconciliationRun)
class ReconciliationRunAdmin(admin.ModelAdmin):
    list_display = ("started_at", "checked", "recovered", "error")
    readonly_fields = [f.name for f in ReconciliationRun._meta.fields]


admin.site.site_header = "Landed admin"
admin.site.site_title = "Landed admin"
admin.site.index_title = "Sellers, payments and payouts"
