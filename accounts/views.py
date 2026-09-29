from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import render, redirect

from courses.models import Course, TopicMastery, Quiz, Topic, Material, QuizAttempt, QuestionResponse, MaterialProgress
from courses import services as course_services
from planning.models import StudyPlan, PracticeTask, AdaptivePracticeSession
from notifications.models import Notification
from .forms import StudentRegisterForm, InstructorRegisterForm
from .models import User, StudentProfile, InstructorProfile


def dashboard(request):
    if not request.user.is_authenticated:
        return render(request, "landing.html")
    if request.user.is_student:
        return redirect("student_dashboard")
    if request.user.is_instructor:
        return redirect("instructor_dashboard")
    return redirect("admin:index")


def register_student(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    available_courses = Course.objects.select_related("instructor")
    if request.method == "POST":
        form = StudentRegisterForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                user = form.save(commit=False)
                user.role = User.Role.STUDENT
                user.save()
                StudentProfile.objects.create(
                    user=user, bio=form.cleaned_data.get("bio", ""),
                    level=form.cleaned_data.get("level") or "beginner",
                )
                course = form.cleaned_data["course"]
                course.students.add(user)
            login(request, user)
            messages.success(request, f"Welcome, {user.username}! You're enrolled in {course.title}.")
            return redirect("student_dashboard")
    else:
        form = StudentRegisterForm()
    return render(request, "register.html", {
        "form": form, "role": "student", "has_courses": available_courses.exists(),
    })


def register_instructor(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    if request.method == "POST":
        form = InstructorRegisterForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                user = form.save(commit=False)
                user.role = User.Role.INSTRUCTOR
                user.save()
                InstructorProfile.objects.create(
                    user=user, bio=form.cleaned_data.get("bio", ""),
                    department=form.cleaned_data.get("department", ""),
                )
                course = Course.objects.create(
                    title=form.cleaned_data["course_title"],
                    description=form.cleaned_data.get("course_description", ""),
                    instructor=user,
                )
            login(request, user)
            messages.success(request, f"Welcome, {user.username}! Your course '{course.title}' is ready.")
            return redirect("instructor_dashboard")
    else:
        form = InstructorRegisterForm()
    return render(request, "register.html", {"form": form, "role": "instructor"})


@login_required
def student_dashboard(request):
    if not request.user.is_student:
        return redirect("dashboard")
    user = request.user
    courses = Course.objects.filter(students=user).prefetch_related("topics", "quizzes", "materials")
    mastery = TopicMastery.objects.filter(student=user).select_related("topic", "topic__course")
    plans = StudyPlan.objects.filter(student=user).prefetch_related("tasks", "tasks__topic")
    notifications = Notification.objects.filter(user=user, is_read=False)[:5]
    topics = Topic.objects.filter(course__in=courses)
    quizzes = Quiz.objects.filter(course__in=courses, is_active=True).select_related("topic", "course")
    my_attempts = {
        a.quiz_id: a for a in
        QuizAttempt.objects.filter(student=user).order_by("quiz_id", "-started_at")
    }
    materials = Material.objects.filter(course__in=courses).select_related("course")
    materials_done_ids = set(
        MaterialProgress.objects.filter(student=user, material__in=materials, is_done=True)
        .values_list("material_id", flat=True)
    )
    recommendations = course_services.compute_recommendations(user)
    active_practice = AdaptivePracticeSession.objects.filter(student=user, is_active=True).select_related("topic").first()

    avg_mastery = round(sum(m.mastery_percent for m in mastery) / len(mastery), 1) if mastery else None

    pending_confirm = {
        "action": request.GET.get("confirm_action"),
        "id": request.GET.get("confirm_id"),
        "field": request.GET.get("confirm_field"),
    }

    return render(request, "student_dashboard.html", {
        "courses": courses,
        "mastery": mastery,
        "avg_mastery": avg_mastery,
        "plans": plans,
        "notifications": notifications,
        "topics": topics,
        "quizzes": quizzes,
        "my_attempts": my_attempts,
        "materials": materials,
        "materials_done_ids": materials_done_ids,
        "recommendations": recommendations,
        "active_practice": active_practice,
        "pending_confirm": pending_confirm,
    })


@login_required
def instructor_dashboard(request):
    if not request.user.is_instructor:
        return redirect("dashboard")
    user = request.user
    courses = Course.objects.filter(instructor=user).prefetch_related(
        "topics", "quizzes", "students", "materials", "modules"
    )
    pending_plans = StudyPlan.objects.filter(
        status=StudyPlan.Status.PENDING, student__courses_enrolled__instructor=user
    ).distinct()
    notifications = Notification.objects.filter(user=user, is_read=False)[:5]

    pending_essays = QuestionResponse.objects.filter(
        needs_review=True, attempt__quiz__course__instructor=user,
    ).select_related("attempt", "attempt__student", "question", "attempt__quiz")

    total_students = sum(c.students.count() for c in courses)

    pending_confirm = {
        "action": request.GET.get("confirm_action"),
        "id": request.GET.get("confirm_id"),
        "field": request.GET.get("confirm_field"),
    }

    return render(request, "instructor_dashboard.html", {
        "courses": courses,
        "pending_plans": pending_plans,
        "notifications": notifications,
        "pending_essays": pending_essays,
        "total_students": total_students,
        "pending_confirm": pending_confirm,
    })
