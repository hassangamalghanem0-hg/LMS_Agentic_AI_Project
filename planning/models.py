from django.conf import settings
from django.db import models
from courses.models import Topic, Quiz


class StudyPlan(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="study_plans")
    title = models.CharField(max_length=200)
    summary = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="reviewed_plans", limit_choices_to={"role": "instructor"},
    )
    review_note = models.TextField(blank=True)
    created_via_agent = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.title} [{self.status}]"


class PracticeTask(models.Model):
    class Priority(models.TextChoices):
        HIGH = "high", "High"
        MEDIUM = "medium", "Medium"
        LOW = "low", "Low"

    plan = models.ForeignKey(StudyPlan, on_delete=models.CASCADE, related_name="tasks")
    topic = models.ForeignKey(Topic, on_delete=models.CASCADE, related_name="practice_tasks")
    recommended_quiz = models.ForeignKey(Quiz, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    description = models.CharField(max_length=300)
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.MEDIUM)
    is_done = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.description} ({'done' if self.is_done else 'open'})"


class AdaptivePracticeSession(models.Model):
    """Server-side state for the AI-Generated Adaptive Practice Mode
    (agents/tools.py: start_adaptive_practice / submit_adaptive_answer /
    end_adaptive_practice). Deliberately a DB row, not a Django session key:
    agent tools only receive (user, **params) -- no request/session access
    -- so this is where the "current question + running difficulty" state
    that a tool needs to read and mutate has to live. Only one row per
    student should be is_active=True at a time (enforced in the tools, not
    the DB, since a student might reasonably have a paused session per
    topic in a later iteration)."""
    class Difficulty(models.TextChoices):
        EASY = "easy", "Easy"
        MEDIUM = "medium", "Medium"
        HARD = "hard", "Hard"

    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="adaptive_sessions")
    topic = models.ForeignKey(Topic, on_delete=models.CASCADE, related_name="adaptive_sessions")
    context_text = models.TextField(blank=True)
    difficulty = models.CharField(max_length=10, choices=Difficulty.choices, default=Difficulty.MEDIUM)
    streak_correct = models.PositiveIntegerField(default=0)
    streak_wrong = models.PositiveIntegerField(default=0)
    questions_asked = models.PositiveIntegerField(default=0)
    questions_correct = models.PositiveIntegerField(default=0)
    current_question_text = models.TextField(blank=True)
    current_choices = models.JSONField(default=list)  # [{"text": str, "is_correct": bool}, ...]
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.student.username} practicing {self.topic.name} ({self.difficulty}, {'active' if self.is_active else 'ended'})"
