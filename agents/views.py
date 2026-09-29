from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponseForbidden
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_POST
from urllib.parse import urlencode
import json

from . import student_agent, instructor_agent
from .presentation import build_display, toast_label


def _coerce(params):
    """POST data is all strings; coerce *_id fields to int, confirm to bool,
    and the 'choices' field (sent as a JSON string by the add-question form) to a list."""
    out = {}
    for k, v in params.items():
        if k in ("confirm", "is_done"):
            out[k] = str(v).lower() in ("1", "true", "on", "yes")
        elif k == "choices":
            if v:
                try:
                    out[k] = json.loads(v)
                except (json.JSONDecodeError, TypeError):
                    out[k] = []
        elif k.endswith("_id") and v not in (None, ""):
            try:
                out[k] = int(v)
            except ValueError:
                out[k] = v
        elif v != "":
            out[k] = v
    return out


# ---------------------------------------------------------------- Student --

@login_required
@require_POST
def student_action(request, action):
    if not request.user.is_student:
        return HttpResponseForbidden("Students only.")
    if action not in student_agent.TOOL_FUNCS:
        return HttpResponseForbidden("Unknown action.")

    params = _coerce(request.POST.dict())
    result = student_agent.call_tool(request.user, action, **params)
    _flash(request, action, result)
    return _redirect_after(request, "student_dashboard", action, params, result)


@login_required
@require_POST
def student_chat(request):
    if not request.user.is_student:
        return HttpResponseForbidden("Students only.")
    payload = json.loads(request.body or "{}")
    result = student_agent.handle_message(request.user, payload.get("message", ""))
    return JsonResponse(result)


# ------------------------------------------------------------- Instructor --

@login_required
@require_POST
def instructor_action(request, action):
    if not request.user.is_instructor:
        return HttpResponseForbidden("Instructors only.")
    if action not in instructor_agent.TOOL_FUNCS:
        return HttpResponseForbidden("Unknown action.")

    params = _coerce(request.POST.dict())
    result = instructor_agent.call_tool(request.user, action, **params)
    _flash(request, action, result)
    return _redirect_after(request, "instructor_dashboard", action, params, result)


@login_required
@require_POST
def instructor_chat(request):
    if not request.user.is_instructor:
        return HttpResponseForbidden("Instructors only.")
    payload = json.loads(request.body or "{}")
    result = instructor_agent.handle_message(request.user, payload.get("message", ""))
    return JsonResponse(result)


def _redirect_after(request, default_view_name, action, params, result):
    # "next" (a full path, e.g. from a detail page) takes priority so actions
    # taken from the new full-screen Analytics / Performance / Study Plan
    # pages land back on that same page instead of always bouncing to the
    # dashboard. Falls back to next_name (a plain URL name) or the dashboard.
    next_path = request.POST.get("next")
    url = next_path if next_path else reverse(request.POST.get("next_name") or default_view_name)
    if result.get("requires_confirmation"):
        id_field = next((k for k in params if k.endswith("_id")), None)
        qs = urlencode({
            "confirm_action": action,
            "confirm_field": id_field or "",
            "confirm_id": params.get(id_field, ""),
        })
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}{qs}"
    return redirect(url)


@login_required
@require_POST
def instructor_upload_material(request):
    if not request.user.is_instructor:
        return HttpResponseForbidden("Instructors only.")
    django_file = request.FILES.get("file")
    params = _coerce(request.POST.dict())
    params["django_file"] = django_file
    result = instructor_agent.call_tool(request.user, "upload_material", **params)
    _flash(request, "upload_material", result)
    return redirect("instructor_dashboard")


def _flash(request, action, result):
    """Keep the banner to a short "it worked / it didn't" notification --
    the actual content (an explanation, a generated quiz, a summary, or any
    other tool's data) is stashed in the session and rendered as a properly
    formatted card on the next page load by the agent_result context
    processor + templates/partials/agent_result.html, instead of being
    crammed into this one-line message as a raw dict repr."""
    if result.get("requires_confirmation"):
        messages.warning(request, f"⚠ {result['message']}")
    elif result.get("ok"):
        messages.success(request, f"✅ {toast_label(action)}")
        request.session["agent_result"] = build_display(action, result.get("data") or {})
    else:
        messages.error(request, f"❌ {result.get('error')}")
