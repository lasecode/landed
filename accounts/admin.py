from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import Business, User


class MemberInline(admin.TabularInline):
    model = User
    fields = ("name", "phone", "role", "is_active")
    readonly_fields = ("phone",)
    extra = 0
    show_change_link = True


@admin.register(Business)
class BusinessAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "status", "payout_bank_name", "payout_account_name", "subaccount_code", "created_at")
    list_filter = ("status",)
    search_fields = ("name", "phone", "payout_account_name", "subaccount_code")
    readonly_fields = ("subaccount_code", "next_invoice_number", "created_at")
    inlines = [MemberInline]
    actions = ["suspend", "activate"]
    fieldsets = (
        (None, {"fields": ("name", "phone", "email", "status", "alert_phone", "notes")}),
        ("Payout (Paystack sub-account)", {"fields": ("payout_bank_name", "payout_bank_code", "payout_account_number",
                                                      "payout_account_name", "subaccount_code", "fee_percent")}),
        ("Invoices", {"fields": ("reminder_days", "due_days", "next_invoice_number", "created_at")}),
    )

    @admin.action(description="Suspend (stop new invoices)")
    def suspend(self, request, qs):
        qs.update(status=Business.Status.SUSPENDED)

    @admin.action(description="Re-activate")
    def activate(self, request, qs):
        qs.exclude(subaccount_code="").update(status=Business.Status.ACTIVE)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    ordering = ("-date_joined",)
    list_display = ("name", "phone", "business", "role", "is_active", "is_staff")
    list_filter = ("role", "is_active", "is_staff")
    search_fields = ("name", "phone", "email")
    fieldsets = (
        (None, {"fields": ("phone", "password", "name", "email")}),
        ("Business", {"fields": ("business", "role")}),
        ("Access", {"fields": ("is_active", "is_staff", "is_superuser", "groups")}),
    )
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("phone", "name", "business", "role", "password1", "password2")}),)
