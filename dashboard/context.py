from django.conf import settings

from payments.models import Payment
from payments.providers import is_test_mode


def app_context(request):
    ctx = {"test_mode": is_test_mode(), "app_name": "Landed"}
    user = getattr(request, "user", None)
    if user and user.is_authenticated and user.business_id:
        ctx["business"] = user.business
        ctx["nav_unmatched"] = Payment.objects.filter(business_id=user.business_id, status=Payment.Status.UNMATCHED).count()
    return ctx
