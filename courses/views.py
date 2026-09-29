from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_POST

from agents import instructor_agent, student_agent
from .models import Course, Quiz, QuizAttempt
from . import services


@login_required
def quiz_detail(request, quiz_id):
    if not request.user.is_student:
        return HttpResponseForbidden("Students only.")
    quiz = get_object_or_404(Quiz, id=quiz_id, is_active=True)
    if not quiz.course.students.filter(id=request.user.id).exists():
        return HttpResponseForbidden("You are not enrolled in this course.")

    existing = QuizAttempt.objects.filter(quiz=quiz, student=request.user).order_by("-started_at").first()
    return render(request, "quiz_detail.html", {"quiz": quiz, "existing": existing})


@login_required
@require_POST
def quiz_submit(request, quiz_id):
    if not request.user.is_student:
        return HttpResponseForbidden("Students only.")
    quiz = get_object_or_404(Quiz, id=quiz_id, is_active=True)
    if not quiz.course.students.filter(id=request.user.id).exists():
        return HttpResponseForbidden("You are not enrolled in this course.")

    answers = {}
    for q in quiz.questions.all():
        if q.question_type == "mcq":
            choice_id = request.POST.get(f"q_{q.id}")
            if choice_id:
                answers[str(q.id)] = {"choice_id": int(choice_id)}
        else:
            text = request.POST.get(f"q_{q.id}", "")
            answers[str(q.id)] = {"text": text}

    attempt = services.submit_attempt(request.user, quiz, answers)
    if attempt.status == QuizAttempt.Status.PENDING_REVIEW:
        messages.warning(request, f"✅ Submitted. MCQ part auto-graded ({attempt.score}%) -- essay answers await instructor grading.")
    else:
        messages.success(request, f"✅ Submitted and graded automatically: {attempt.score}%")
    return redirect("student_dashboard")


# --------------------------------------------------------------------------
# Full-screen instructor pages: these render on the whole page (with charts)
# instead of the old behaviour of dumping the tool's raw output into a
# one-line flash-message banner.
# --------------------------------------------------------------------------

@login_required
def course_analytics_view(request, course_id):
    if not request.user.is_instructor:
        return HttpResponseForbidden("Instructors only.")
    course = get_object_or_404(Course, id=course_id, instructor=request.user)
    result = instructor_agent.call_tool(request.user, "get_course_analytics", course_id=course.id)
    if not result.get("ok"):
        messages.error(request, f"❌ Could not load analytics: {result.get('error')}")
        return redirect("instructor_dashboard")
    return render(request, "course_analytics.html", {"course": course, "data": result["data"]})


@login_required
def student_analytics_view(request):
    if not request.user.is_student:
        return HttpResponseForbidden("Students only.")
    result = student_agent.call_tool(request.user, "get_learning_dashboard")
    if not result.get("ok"):
        messages.error(request, f"❌ Could not load analytics: {result.get('error')}")
        return redirect("student_dashboard")
    return render(request, "student_analytics.html", {"data": result["data"]})


@login_required
def student_performance_view(request, course_id, student_id):
    if not request.user.is_instructor:
        return HttpResponseForbidden("Instructors only.")
    course = get_object_or_404(Course, id=course_id, instructor=request.user)
    result = instructor_agent.call_tool(
        request.user, "get_student_performance", course_id=course.id, student_id=student_id,
    )
    if not result.get("ok"):
        messages.error(request, f"❌ Could not load performance: {result.get('error')}")
        return redirect("instructor_dashboard")
    return render(request, "student_performance.html", {"course": course, "data": result["data"]})
