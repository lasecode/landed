from django.shortcuts import redirect


class OnboardingMiddleware:
    """Landed team accounts (no business) go to the admin instead of the seller dashboard."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if (user and user.is_authenticated and user.business_id is None and user.is_staff
                and request.path.startswith("/app/")):
            return redirect("/admin/")
        return self.get_response(request)
