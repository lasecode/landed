from django import template

from payments.money import naira as _naira

register = template.Library()


@register.filter
def naira(kobo, arg=""):
    return _naira(kobo or 0, short=(arg == "short"))


@register.filter
def initials(name):
    return "".join(w[0] for w in (name or "").split()[:2]).upper()


@register.filter
def phone(p):
    p = p or ""
    return f"+{p}" if p and not p.startswith("+") else p


@register.simple_tag
def status_label(s):
    return {"paid": "Paid", "part_paid": "Part-paid", "unpaid": "Unpaid", "cancelled": "Cancelled",
            "matched": "Matched", "unmatched": "Needs assigning", "refunded": "Refunded"}.get(s, s)


_ICONS = {
    "home": '<path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V20h14V9.5"/>',
    "invoice": '<path d="M6 3h9l4 4v14H6z"/><path d="M14 3v5h5M9 13h6M9 17h4"/>',
    "users": '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c.7-3.5 3.3-5.5 6.5-5.5s5.8 2 6.5 5.5"/><path d="M16 4.5a3.5 3.5 0 0 1 0 7M18.5 14.8c1.7.8 2.7 2.6 3 5.2"/>',
    "alert": '<path d="M12 3 2 20h20z"/><path d="M12 10v4M12 17h.01"/>',
    "chart": '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1"/>',
    "chat": '<path d="M21 12a8.5 8.5 0 0 1-12.4 7.6L3 21l1.5-5.2A8.5 8.5 0 1 1 21 12z"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "x": '<path d="M6 6l12 12M18 6 6 18"/>',
    "check": '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
    "copy": '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    "menu": '<path d="M4 7h16M4 12h16M4 17h16"/>',
    "in": '<path d="M12 4v12M6 10l6 6 6-6M5 20h14"/>',
    "send": '<path d="M22 2 11 13M22 2l-7 20-4-9-9-4z"/>',
    "out": '<path d="M15 3h6v6M10 14 21 3M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
    "logout": '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/>',
    "download": '<path d="M12 3v12M7 10l5 5 5-5M4 21h16"/>',
    "bank": '<path d="M3 10h18L12 4zM5 10v8M9.5 10v8M14.5 10v8M19 10v8M3 21h18"/>',
}


@register.simple_tag
def icon(name, size=None):
    from django.utils.safestring import mark_safe

    style = f' style="width:{size}px;height:{size}px"' if size else ""
    return mark_safe(f'<svg class="i" viewBox="0 0 24 24" aria-hidden="true"{style}>{_ICONS.get(name, "")}</svg>')


@register.filter
def first_word(s):
    return (s or "").split()[0] if (s or "").split() else ""


@register.filter
def wa(text):
    """WhatsApp-style *bold* to HTML, safely."""
    import re

    from django.utils.html import escape
    from django.utils.safestring import mark_safe

    return mark_safe(re.sub(r"\*([^*\n]+)\*", r"<b>\1</b>", escape(text or "")))
