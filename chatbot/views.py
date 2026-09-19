import json
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from . import services


@login_required
@require_POST
def ask(request):
    payload = json.loads(request.body or "{}")
    reply = services.answer(request.user, payload.get("message", ""))
    return JsonResponse({"reply": reply})
