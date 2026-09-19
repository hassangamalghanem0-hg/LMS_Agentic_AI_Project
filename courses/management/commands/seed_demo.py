import random
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model

from accounts.models import StudentProfile, InstructorProfile
from courses.models import (
    Course, Module, Lesson, Topic, Quiz, Question, Choice,
    QuizAttempt, QuestionResponse, Material,
)
from courses.services import grade_attempt
from courses import text_extraction
from planning.models import StudyPlan, PracticeTask

User = get_user_model()

SAMPLE_LECTURE_TEXT = """Agentic AI systems differ from simple chatbots because they can take
real actions in the world through tools, not just answer questions. A tool is a well-defined
function with a name, a description, and a schema for its parameters. When an agent decides to
use a tool, it calls that function with specific arguments, and the result is fed back into the
conversation so the agent can decide what to do next.

Role-based access control, or RBAC, is essential in agentic systems: every tool call must check
whether the calling user is allowed to perform that action on that specific resource, not just
whether the user is logged in. Destructive actions such as deleting data or canceling something
that is already running should require explicit confirmation before they execute, typically
through a two-step confirmation flow.

An audit log that records every tool call, whether it succeeded, was denied, or is pending
confirmation, is critical for debugging and for building trust in an agentic system. Separating
a read-only chatbot from an action-taking agent is a common and important architectural pattern,
because it makes the security boundary between 'answering a question' and 'changing the database'
explicit in the code rather than just in the UI."""


class Command(BaseCommand):
    help = "Seed demo users, a course, topics, quizzes, materials and simulated history."

    def handle(self, *args, **options):
        self.stdout.write("Seeding demo data...")

        admin, _ = User.objects.get_or_create(
            username="admin", defaults={"role": "admin", "is_staff": True, "is_superuser": True}
        )
        admin.set_password("admin123")
        admin.save()

        instructor, created = User.objects.get_or_create(username="instructor1", defaults={"role": "instructor", "email": "instructor1@example.com"})
        instructor.set_password("instructor123")
        instructor.role = "instructor"
        instructor.save()
        InstructorProfile.objects.get_or_create(user=instructor, defaults={"department": "Computer Science"})

        students = []
        for i in range(1, 4):
            s, _ = User.objects.get_or_create(username=f"student{i}", defaults={"role": "student", "email": f"student{i}@example.com"})
            s.set_password("student123")
            s.role = "student"
            s.save()
            StudentProfile.objects.get_or_create(user=s, defaults={"level": "beginner"})
            students.append(s)

        course, _ = Course.objects.get_or_create(
            title="Full-Stack Python Development",
            instructor=instructor,
            defaults={"description": "Django, REST APIs, and agentic AI integration."},
        )
        course.students.set(students)

        module, _ = Module.objects.get_or_create(course=course, title="Backend Fundamentals", order=1)
        Lesson.objects.get_or_create(module=module, title="Django Models & ORM", order=1, defaults={"content": "..."})
        Lesson.objects.get_or_create(module=module, title="Django Views & URLs", order=2, defaults={"content": "..."})

        topic_names = ["Django ORM", "REST APIs", "Authentication & RBAC", "Agentic AI Tool Calling"]
        topics = []
        for name in topic_names:
            t, _ = Topic.objects.get_or_create(course=course, name=name)
            topics.append(t)
        agentic_topic = topics[-1]

        quizzes = []
        for t in topics:
            quiz, created = Quiz.objects.get_or_create(
                course=course, topic=t, title=f"{t.name} Quiz",
                defaults={"created_by": instructor},
            )
            quizzes.append(quiz)
            if created:
                for i in range(1, 4):
                    q = Question.objects.create(quiz=quiz, topic=t, text=f"Sample question {i} about {t.name}?", order=i)
                    correct_idx = random.randint(0, 2)
                    for j in range(3):
                        Choice.objects.create(question=q, text=f"Option {j+1}", is_correct=(j == correct_idx))

        # Demo material with real extractable text, so the AI tools have something to read.
        material, created = Material.objects.get_or_create(
            course=course, title="Lecture 5: Agentic AI Architecture",
            defaults={"uploaded_by": instructor, "kind": "txt"},
        )
        if created or not material.file:
            material.file.save("lecture5_agentic_ai.txt", ContentFile(SAMPLE_LECTURE_TEXT.encode("utf-8")), save=False)
            text, status = text_extraction.extract_text(material.file, "txt")
            material.extracted_text = text
            material.extraction_status = status
            material.save()

        # A quiz with one essay question so the Grading Queue has something in it.
        essay_quiz, created = Quiz.objects.get_or_create(
            course=course, topic=agentic_topic, title="Agentic AI — Short Answer Check",
            defaults={"created_by": instructor, "generated_from_material": material},
        )
        if created:
            eq = Question.objects.create(
                quiz=essay_quiz, topic=agentic_topic, question_type=Question.Type.ESSAY, order=1,
                text="In your own words, explain why destructive agent tools should require a two-step confirmation.",
            )
            # student1 has already answered it and is waiting on the instructor to grade it
            attempt = QuizAttempt.objects.create(quiz=essay_quiz, student=students[0], status=QuizAttempt.Status.PENDING_REVIEW)
            QuestionResponse.objects.create(
                attempt=attempt, question=eq, needs_review=True,
                answer_text="Because a single accidental click could delete real data or cancel something "
                            "that's already running, so the agent should double-check with the user first.",
            )

        # Simulate quiz history so TopicMastery + analytics have real numbers
        for student in students:
            for quiz in quizzes:
                if random.random() < 0.7:  # not every student takes every quiz
                    attempt = QuizAttempt.objects.create(quiz=quiz, student=student)
                    for q in quiz.questions.all():
                        choices = list(q.choices.all())
                        correct_choice = next(c for c in choices if c.is_correct)
                        pick_correct = random.random() < random.choice([0.3, 0.6, 0.9])
                        chosen = correct_choice if pick_correct else random.choice(choices)
                        QuestionResponse.objects.create(
                            attempt=attempt, question=q, selected_choice=chosen,
                            is_correct=(chosen.id == correct_choice.id),
                        )
                    grade_attempt(attempt)

        # One example pending study plan for the review queue demo
        StudyPlan.objects.get_or_create(
            student=students[0], title=f"Study plan for {course.title}",
            defaults={"summary": "Focus areas based on current mastery: REST APIs (40%), Authentication & RBAC (55%).", "status": "pending"},
        )

        self.stdout.write(self.style.SUCCESS(
            "Done. Login as instructor1/instructor123 or student1/student123 (also student2, student3)."
        ))
