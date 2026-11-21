from django.contrib import admin

from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("created_at", "business", "to", "template", "status", "error")
    list_filter = ("status", "template")
    search_fields = ("to", "body")
    readonly_fields = [f.name for f in Notification._meta.fields]
