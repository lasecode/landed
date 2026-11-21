from __future__ import annotations

from django import forms
from django.contrib.auth import password_validation
from django.contrib.auth.forms import AuthenticationForm

from .models import Business, User, normalize_phone


def _phone(value: str) -> str:
    p = normalize_phone(value)
    if len(p) < 12:
        raise forms.ValidationError("Enter a valid Nigerian phone number, e.g. 0803 123 4567")
    return p


class PhoneLoginForm(AuthenticationForm):
    username = forms.CharField(label="Phone number", widget=forms.TextInput(attrs={
        "autocomplete": "tel", "inputmode": "tel", "placeholder": "0803 123 4567", "autofocus": True}))

    def clean_username(self):
        return normalize_phone(self.cleaned_data["username"])

    error_messages = {
        "invalid_login": "That phone number and password don't match.",
        "inactive": "This login has been switched off. Ask the business owner.",
    }


class SignupForm(forms.Form):
    business_name = forms.CharField(max_length=120, label="Business name",
                                    widget=forms.TextInput(attrs={"placeholder": "Adaeze Foods"}))
    name = forms.CharField(max_length=120, label="Your name", widget=forms.TextInput(attrs={"placeholder": "Adaeze Okafor"}))
    phone = forms.CharField(label="WhatsApp number", help_text="Payment alerts go here.",
                            widget=forms.TextInput(attrs={"inputmode": "tel", "placeholder": "0803 123 4567"}))
    email = forms.EmailField(required=False, label="Email (optional)", help_text="Used only to reset your password.")
    password = forms.CharField(widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}), min_length=8)

    def clean_phone(self):
        p = _phone(self.cleaned_data["phone"])
        if User.objects.filter(phone=p).exists():
            raise forms.ValidationError("That number already has an account. Sign in instead.")
        return p

    def clean_password(self):
        password_validation.validate_password(self.cleaned_data["password"])
        return self.cleaned_data["password"]

    def save(self) -> User:
        d = self.cleaned_data
        b = Business.objects.create(name=d["business_name"].strip(), phone=d["phone"], email=d["email"],
                                    alert_phone=d["phone"])
        return User.objects.create_user(d["phone"], d["password"], name=d["name"].strip(), email=d["email"],
                                        business=b, role=User.Role.OWNER)


class StaffForm(forms.Form):
    name = forms.CharField(max_length=120)
    phone = forms.CharField(widget=forms.TextInput(attrs={"inputmode": "tel"}))
    password = forms.CharField(label="Temporary password", min_length=8)
    role = forms.ChoiceField(choices=User.Role.choices, initial=User.Role.STAFF)

    def __init__(self, *args, business=None, **kw):
        super().__init__(*args, **kw)
        self.business = business

    def clean_phone(self):
        p = _phone(self.cleaned_data["phone"])
        existing = User.objects.filter(phone=p).first()
        if existing and not (existing.business_id == self.business.id and not existing.is_active):
            raise forms.ValidationError("That number already has a login")
        return p

    def save(self) -> User:
        d = self.cleaned_data
        u = User.objects.filter(phone=d["phone"]).first()
        if u:  # re-adding a removed login (also how an owner resets a staff password)
            u.name, u.role, u.is_active = d["name"], d["role"], True
            u.set_password(d["password"])
            u.save()
            return u
        return User.objects.create_user(d["phone"], d["password"], name=d["name"], business=self.business, role=d["role"])


class BusinessForm(forms.ModelForm):
    class Meta:
        model = Business
        fields = ["name", "alert_phone", "email", "reminder_days", "due_days"]
        labels = {"name": "Business name", "alert_phone": "WhatsApp number for payment alerts",
                  "reminder_days": "Send reminders after (days)", "due_days": "Invoices are due after (days)"}
        help_texts = {"reminder_days": "Days after the invoice is sent, e.g. 2,5. Leave empty for no reminders.",
                      "name": "Customers see this on every invoice and receipt."}

    def clean_alert_phone(self):
        v = self.cleaned_data["alert_phone"]
        return _phone(v) if v else ""

    def clean_reminder_days(self):
        parts = [p.strip() for p in (self.cleaned_data["reminder_days"] or "").split(",") if p.strip()]
        if not all(p.isdigit() and 0 < int(p) <= 90 for p in parts):
            raise forms.ValidationError("Use whole days separated by commas, e.g. 2,5,10")
        return ",".join(str(int(p)) for p in sorted(set(parts), key=int))


class PayoutForm(forms.Form):
    bank_code = forms.ChoiceField(label="Bank")
    account_number = forms.CharField(label="Account number", min_length=10, max_length=10,
                                     widget=forms.TextInput(attrs={"inputmode": "numeric", "placeholder": "10 digits"}))

    def __init__(self, *args, banks=(), **kw):
        super().__init__(*args, **kw)
        self.fields["bank_code"].choices = [("", "Choose your bank")] + [(b.code, b.name) for b in banks]
