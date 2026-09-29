from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import render, get_object_or_404

from .models import StudyPlan


@login_required
def study_plan_detail(request, plan_id):
    plan = get_object_or_404(StudyPlan, id=plan_id)
    is_owner = plan.student_id == request.user.id
    is_reviewing_instructor = (
        request.user.is_instructor
        and plan.student.courses_enrolled.filter(instructor=request.user).exists()
    )
    if not (is_owner or is_reviewing_instructor):
        return HttpResponseForbidden("You don't have access to this study plan.")

    tasks = plan.tasks.select_related("topic").all()
    total = tasks.count()
    done = sum(1 for t in tasks if t.is_done)
    progress_percent = round((done / total) * 100) if total else 0

    pending_confirm = {
        "action": request.GET.get("confirm_action"),
        "id": request.GET.get("confirm_id"),
        "field": request.GET.get("confirm_field"),
    }

    return render(request, "study_plan_detail.html", {
        "plan": plan,
        "tasks": tasks,
        "total_tasks": total,
        "done_tasks": done,
        "progress_percent": progress_percent,
        "is_owner": is_owner,
        "is_reviewing_instructor": is_reviewing_instructor,
        "pending_confirm": pending_confirm if pending_confirm["action"] else None,
    })
