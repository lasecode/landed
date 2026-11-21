from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path

from accounts.forms import PhoneLoginForm
from dashboard import views as v
from messaging.views import whatsapp_webhook
from payments.views import paystack_webhook

urlpatterns = [
    path("", v.home, name="home"),
    path("healthz", v.healthz),
    path("admin/", admin.site.urls),
    # auth
    path("login/", auth_views.LoginView.as_view(authentication_form=PhoneLoginForm, redirect_authenticated_user=True),
         name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("signup/", v.signup, name="signup"),
    path("password-reset/", auth_views.PasswordResetView.as_view(), name="password_reset"),
    path("password-reset/sent/", auth_views.PasswordResetDoneView.as_view(), name="password_reset_done"),
    path("reset/<uidb64>/<token>/", auth_views.PasswordResetConfirmView.as_view(), name="password_reset_confirm"),
    path("reset/done/", auth_views.PasswordResetCompleteView.as_view(), name="password_reset_complete"),
    # seller dashboard
    path("app/", v.overview, name="overview"),
    path("app/setup/payout/", v.payout_setup, name="payout_setup"),
    path("app/invoices/", v.invoices, name="invoices"),
    path("app/invoices/new/", v.invoice_new, name="invoice_new"),
    path("app/invoices/<int:pk>/", v.invoice_detail, name="invoice_detail"),
    path("app/invoices/<int:pk>/<str:action>/", v.invoice_action, name="invoice_action"),
    path("app/customers/", v.customers, name="customers"),
    path("app/customers/new/", v.customer_new, name="customer_new"),
    path("app/customers/<int:pk>/", v.customer_detail, name="customer_detail"),
    path("app/customers/<int:pk>/account/", v.customer_account, name="customer_account"),
    path("app/unmatched/", v.unmatched, name="unmatched"),
    path("app/payments/<int:pk>/assign/", v.payment_assign, name="payment_assign"),
    path("app/payments/<int:pk>/refund/", v.payment_refund, name="payment_refund"),
    path("app/reports/", v.reports, name="reports"),
    path("app/reports/export.csv", v.export_csv, name="export_csv"),
    path("app/settings/", v.settings_view, name="settings"),
    path("app/settings/staff/<int:pk>/remove/", v.staff_remove, name="staff_remove"),
    path("app/bot/", v.bot_tester, name="bot"),
    # customer-facing
    path("pay/<str:token>/", v.public_invoice, name="public_invoice"),
    path("pay/<str:token>/account/", v.public_pay, name="public_pay"),
    path("pay/<str:token>/status/", v.public_status, name="public_status"),
    # webhooks
    path("webhooks/paystack/", paystack_webhook, name="paystack_webhook"),
    path("webhooks/whatsapp/", whatsapp_webhook, name="whatsapp_webhook"),
]
