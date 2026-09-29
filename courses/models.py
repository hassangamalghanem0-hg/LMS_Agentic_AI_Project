from django.conf import settings
from django.db import models


class Course(models.Model):
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    instructor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="courses_taught", limit_choices_to={"role": "instructor"},
    )
    students = models.ManyToManyField(
        settings.AUTH_USER_MODEL, related_name="courses_enrolled",
        blank=True, limit_choices_to={"role": "student"},
    )
    created_at = models.DateTimeField(auto_now_add=True)

    ai_summary = models.TextField(blank=True)
    ai_summary_generated_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.title


class Module(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="modules")
    title = models.CharField(max_length=200)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return f"{self.course.title} / {self.title}"


class Lesson(models.Model):
    module = models.ForeignKey(Module, on_delete=models.CASCADE, related_name="lessons")
    title = models.CharField(max_length=200)
    content = models.TextField(blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return self.title


class Topic(models.Model):
    """A tagging unit used by TopicMastery + Questions so the agent can
    reason about a student's strengths/weaknesses per-topic."""
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="topics")
    name = models.CharField(max_length=150)

    class Meta:
        unique_together = ("course", "name")

    def __str__(self):
        return f"{self.name} ({self.course.title})"


class Quiz(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="quizzes")
    topic = models.ForeignKey(Topic, on_delete=models.SET_NULL, null=True, blank=True, related_name="quizzes")
    title = models.CharField(max_length=200)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="quizzes_created")
    created_via_agent = models.BooleanField(default=False)
    generated_from_material = models.ForeignKey(
        "Material", on_delete=models.SET_NULL, null=True, blank=True, related_name="generated_quizzes"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.title


class Question(models.Model):
    class Type(models.TextChoices):
        MCQ = "mcq", "Multiple choice (auto-graded)"
        ESSAY = "essay", "Essay / short answer (manually graded)"

    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name="questions")
    topic = models.ForeignKey(Topic, on_delete=models.CASCADE, related_name="questions")
    text = models.TextField()
    question_type = models.CharField(max_length=10, choices=Type.choices, default=Type.MCQ)
    order = models.PositiveIntegerField(default=0)
    generated_by_ai = models.BooleanField(default=False)

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return self.text[:60]


class Choice(models.Model):
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="choices")
    text = models.CharField(max_length=300)
    is_correct = models.BooleanField(default=False)

    def __str__(self):
        return self.text[:60]


class QuizAttempt(models.Model):
    class Status(models.TextChoices):
        IN_PROGRESS = "in_progress", "In progress"
        PENDING_REVIEW = "pending_review", "Awaiting instructor grading"
        GRADED = "graded", "Graded"

    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name="attempts")
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="quiz_attempts")
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    score = models.FloatField(null=True, blank=True)  # percentage 0-100, recomputed as essays get graded
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.IN_PROGRESS)

    def __str__(self):
        return f"{self.student.username} -> {self.quiz.title} ({self.score})"


class QuestionResponse(models.Model):
    attempt = models.ForeignKey(QuizAttempt, on_delete=models.CASCADE, related_name="responses")
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="responses")
    selected_choice = models.ForeignKey(Choice, on_delete=models.SET_NULL, null=True, blank=True)
    answer_text = models.TextField(blank=True)  # essay/short-answer response
    is_correct = models.BooleanField(default=False)  # mcq: auto-set; essay: set once graded
    needs_review = models.BooleanField(default=False)  # True for ungraded essay responses
    manual_score = models.FloatField(null=True, blank=True)  # 0.0-1.0, instructor-assigned for essays
    graded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="graded_responses", limit_choices_to={"role": "instructor"},
    )
    feedback = models.CharField(max_length=500, blank=True)
    graded_at = models.DateTimeField(null=True, blank=True)


def material_upload_path(instance, filename):
    return f"course_{instance.course_id}/materials/{filename}"


class Material(models.Model):
    """A file an instructor uploads (lecture slides, PDF notes, etc). The
    Instructor Agent can read its extracted_text to generate a summary or
    a quiz automatically (see agents/tools.py + courses/ai_generation.py)."""

    class Kind(models.TextChoices):
        PDF = "pdf", "PDF"
        DOCX = "docx", "Word document"
        PPTX = "pptx", "Slides"
        TXT = "txt", "Text"
        VIDEO = "video", "Video lecture"
        OTHER = "other", "Other"

    class ExtractionStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        DONE = "done", "Done"
        NOT_APPLICABLE = "n/a", "Not applicable"
        UNSUPPORTED = "unsupported", "Unsupported format"
        FAILED = "failed", "Failed"

    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="materials")
    module = models.ForeignKey(Module, on_delete=models.SET_NULL, null=True, blank=True, related_name="materials")
    lesson = models.ForeignKey(Lesson, on_delete=models.SET_NULL, null=True, blank=True, related_name="materials")
    title = models.CharField(max_length=200)
    file = models.FileField(upload_to=material_upload_path)
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.OTHER)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="materials_uploaded")

    extracted_text = models.TextField(blank=True)
    extraction_status = models.CharField(max_length=15, choices=ExtractionStatus.choices, default=ExtractionStatus.PENDING)

    ai_summary = models.TextField(blank=True)
    ai_summary_generated_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.title} ({self.course.title})"


class MaterialProgress(models.Model):
    """Tracks whether a student has marked a Material as studied. Powers the
    "Completed Materials" metric on the student Learning Analytics dashboard
    (agents/tools.py:get_learning_dashboard) -- a lightweight engagement
    signal, not a graded outcome."""
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="material_progress")
    material = models.ForeignKey(Material, on_delete=models.CASCADE, related_name="progress_records")
    is_done = models.BooleanField(default=False)
    done_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ("student", "material")

    def __str__(self):
        return f"{self.student.username} / {self.material.title}: {'done' if self.is_done else 'not done'}"


class TopicMastery(models.Model):
    """Rolling mastery percentage per (student, topic). Updated whenever a
    quiz attempt is graded. The Student Agent reads this to build plans."""
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="topic_mastery")
    topic = models.ForeignKey(Topic, on_delete=models.CASCADE, related_name="mastery_records")
    mastery_percent = models.FloatField(default=0)
    attempts_count = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("student", "topic")

    def __str__(self):
        return f"{self.student.username} / {self.topic.name}: {self.mastery_percent:.0f}%"
