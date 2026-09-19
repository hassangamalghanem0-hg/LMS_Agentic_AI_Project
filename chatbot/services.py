"""Read-only Chatbot. Deliberately does NOT import agents.tools or write to
AgentActionLog -- the SRS requires this to be a fully separate surface from
the two action-taking agents. It only ever reads data belonging to the
current user and answers simple questions; it can never create, delete, or
approve anything.
"""
import logging

import llm_client
from courses.models import TopicMastery, QuizAttempt, Course

logger = logging.getLogger(__name__)


def answer(user, message):
    text = message.lower()

    if user.is_student:
        if any(k in text for k in ["grade", "score", "درجت", "درجة"]):
            attempts = QuizAttempt.objects.filter(student=user, completed_at__isnull=False).order_by("-completed_at")[:5]
            if not attempts:
                return "You haven't completed any quizzes yet."
            lines = "; ".join(f"{a.quiz.title}: {a.score:.0f}%" for a in attempts)
            return f"Your most recent scores: {lines}."

        if any(k in text for k in ["mastery", "topic", "إتقان", "مستواي"]):
            records = TopicMastery.objects.filter(student=user).select_related("topic")
            if not records:
                return "No topic mastery data yet -- take a quiz first."
            lines = "; ".join(f"{r.topic.name}: {r.mastery_percent:.0f}%" for r in records)
            return f"Your topic mastery: {lines}."

        if any(k in text for k in ["course", "enrolled", "كورس"]):
            courses = Course.objects.filter(students=user)
            names = ", ".join(c.title for c in courses) or "none yet"
            return f"You're enrolled in: {names}."

    if user.is_instructor:
        if any(k in text for k in ["course", "كورس"]):
            courses = Course.objects.filter(instructor=user)
            names = ", ".join(c.title for c in courses) or "none yet"
            return f"You teach: {names}."

        if any(k in text for k in ["student", "طلاب", "طالب"]):
            courses = Course.objects.filter(instructor=user)
            total = sum(c.students.count() for c in courses)
            return f"You have {total} students across {courses.count()} course(s)."

    return _general_answer(message)


def _general_answer(message):
    """Anything the read-only lookups above didn't cover goes to the model --
    through the shared resilient client, so a rate-limited key degrades to the
    canned reply instantly instead of stalling the request first."""
    if llm_client.llm_available():
        try:
            return llm_client.generate_text(
                system=(
                    "You are a friendly, read-only LMS help chatbot. You have no tools and "
                    "cannot take any action (no creating/deleting/approving anything) -- if "
                    "asked to do something, tell the user to use the Student/Instructor Agent "
                    "actions in their dashboard instead. Keep answers short. Reply in the same "
                    "language the user wrote in (including Egyptian Arabic)."
                ),
                user_content=message,
                max_tokens=300,
            )
        except Exception as e:  # unavailable, quota, network -- never a 500
            logger.warning("Chatbot model call failed, using the offline reply: %s", e)

    return (
        "I'm the read-only help assistant -- I can tell you about your grades, topic "
        "mastery, or your courses. For actions like creating a study plan or a quiz, "
        "ask the Student/Instructor Agent instead (it works even without an AI key)."
    )
