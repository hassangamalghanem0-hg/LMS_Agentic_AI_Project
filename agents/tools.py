"""Tool implementations for the two agents described in the SRS.

Every tool:
  - takes `user` (the authenticated caller) plus keyword params
  - checks ownership/role itself (defence in depth, on top of the agent
    router only exposing the right tool set per role)
  - is wrapped with @agent_tool so every call is written to AgentActionLog
  - returns {"ok": True, "data": ...} or {"ok": False, "error": ..., "code": ...}

Student Agent tools operate only on the calling student's own data.
Instructor Agent tools operate only on courses the calling instructor owns.
"""
from django.db.models import Avg
from django.utils import timezone

from courses.models import (
    Course, Module, Topic, Quiz, Question, Choice, QuizAttempt, QuestionResponse,
    TopicMastery, Material, MaterialProgress,
)
from courses import services as course_services
from courses import text_extraction, ai_generation
from planning.models import StudyPlan, PracticeTask, AdaptivePracticeSession
from notifications.services import notify, notify_many
from .decorators import agent_tool
from .exceptions import ToolPermissionError, ToolValidationError

STUDENT_AGENT = "student_agent"
INSTRUCTOR_AGENT = "instructor_agent"


# --------------------------------------------------------------------------
# Student Agent tools
# --------------------------------------------------------------------------

@agent_tool(STUDENT_AGENT)
def get_performance(user, **params):
    if not user.is_student:
        raise ToolPermissionError("Only students can call get_performance.")
    records = TopicMastery.objects.filter(student=user).select_related("topic", "topic__course")
    return {
        "topics": [
            {
                "topic": r.topic.name,
                "course": r.topic.course.title,
                "mastery_percent": round(r.mastery_percent, 1),
                "attempts": r.attempts_count,
            }
            for r in records
        ]
    }


@agent_tool(STUDENT_AGENT)
def create_study_plan(user, **params):
    if not user.is_student:
        raise ToolPermissionError("Only students can call create_study_plan.")
    course_id = params.get("course_id")
    course = Course.objects.filter(id=course_id, students=user).first()
    if not course:
        raise ToolValidationError("Course not found or you are not enrolled in it.")

    all_mastery = list(TopicMastery.objects.filter(student=user, topic__course=course).select_related("topic"))
    covered_topic_ids = {m.topic_id for m in all_mastery}
    weak_topics = sorted(all_mastery, key=lambda m: m.mastery_percent)[:3]
    untested_topics = list(course.topics.exclude(id__in=covered_topic_ids)[:3])

    # Adaptive Learning: a priority (high/medium/low), not just a flat topic
    # list -- weak topics are ranked by how far below mastery they are;
    # never-attempted topics default to "medium" (unknown risk, worth a
    # first pass, but not yet a confirmed weak spot). See
    # courses.services.topic_priority for the thresholds.
    priority_by_topic_id = {m.topic_id: course_services.topic_priority(m.mastery_percent) for m in weak_topics}
    for t in untested_topics:
        priority_by_topic_id[t.id] = PracticeTask.Priority.MEDIUM

    # Build a clear, structured, multi-section summary (not one run-on sentence)
    # so the plan reads well as a standalone document, not just a chat reply.
    sections = [f"Study Plan — {course.title}", ""]
    if weak_topics:
        sections.append("Focus areas (based on your current mastery):")
        for m in weak_topics:
            sections.append(
                f"  • [{priority_by_topic_id[m.topic_id].upper()}] {m.topic.name} — "
                f"currently at {m.mastery_percent:.0f}% mastery"
            )
    else:
        sections.append("Focus areas: no quiz history yet in this course.")
    if untested_topics:
        sections.append("")
        sections.append("Not yet attempted (worth a first pass):")
        for t in untested_topics:
            sections.append(f"  • [MEDIUM] {t.name}")
    sections.append("")
    sections.append(
        "Suggested approach: tackle HIGH priority topics first, then MEDIUM, then LOW. Work "
        "through the checklist below one task at a time, then take the recommended quiz for "
        "each topic to update your mastery score."
    )
    summary = "\n".join(sections)

    plan = StudyPlan.objects.create(
        student=user,
        title=f"Study plan for {course.title}",
        summary=summary,
        status=StudyPlan.Status.PENDING,
        created_via_agent=True,
    )

    # Make the plan immediately actionable: seed one practice task per focus
    # topic instead of leaving the student with a bare summary to act on.
    focus_topics = [m.topic for m in weak_topics] + untested_topics
    for topic in focus_topics:
        PracticeTask.objects.create(
            plan=plan, topic=topic,
            description=f"Review & practice: {topic.name}",
            priority=priority_by_topic_id.get(topic.id, PracticeTask.Priority.MEDIUM),
        )

    notify_many(
        _instructors_for_course(course),
        f"{user.username} submitted a new study plan for '{course.title}' awaiting your review.",
    )

    return {
        "plan_id": plan.id,
        "status": plan.status,
        "summary": plan.summary,
        "weak_topic_ids": [m.topic_id for m in weak_topics],
        "seeded_task_count": len(focus_topics),
    }


def _instructors_for_course(course):
    """A course only ever has one instructor today, but keep this as a list
    lookup so notifying works unchanged if that ever becomes M2M."""
    return [course.instructor] if course.instructor_id else []


@agent_tool(STUDENT_AGENT)
def create_practice_task(user, **params):
    if not user.is_student:
        raise ToolPermissionError("Only students can call create_practice_task.")
    plan = StudyPlan.objects.filter(id=params.get("plan_id"), student=user).first()
    if not plan:
        raise ToolValidationError("Study plan not found or does not belong to you.")
    topic = Topic.objects.filter(id=params.get("topic_id")).first()
    if not topic:
        raise ToolValidationError("Topic not found.")
    description = params.get("description") or f"Practice: {topic.name}"
    task = PracticeTask.objects.create(plan=plan, topic=topic, description=description)
    return {"task_id": task.id, "plan_id": plan.id, "topic": topic.name, "description": task.description}


@agent_tool(STUDENT_AGENT)
def recommend_quiz(user, **params):
    if not user.is_student:
        raise ToolPermissionError("Only students can call recommend_quiz.")
    topic = Topic.objects.filter(id=params.get("topic_id")).first()
    if not topic:
        raise ToolValidationError("Topic not found.")
    quiz = Quiz.objects.filter(topic=topic, is_active=True).order_by("?").first()
    if not quiz:
        return {"quiz": None, "message": f"No active quiz found for topic '{topic.name}' yet."}
    return {"quiz_id": quiz.id, "title": quiz.title, "topic": topic.name}


@agent_tool(STUDENT_AGENT, destructive=True)
def delete_practice_task(user, **params):
    if not user.is_student:
        raise ToolPermissionError("Only students can call delete_practice_task.")
    task = PracticeTask.objects.filter(id=params.get("task_id"), plan__student=user).first()
    if not task:
        raise ToolValidationError("Practice task not found or does not belong to you.")
    task_id = task.id
    task.delete()
    return {"deleted_task_id": task_id}


@agent_tool(STUDENT_AGENT)
def mark_task_done(user, **params):
    """Toggle a practice task's completion state. Not destructive (fully
    reversible), so it doesn't need the confirm flow."""
    if not user.is_student:
        raise ToolPermissionError("Only students can call mark_task_done.")
    task = PracticeTask.objects.filter(id=params.get("task_id"), plan__student=user).first()
    if not task:
        raise ToolValidationError("Practice task not found or does not belong to you.")
    is_done = params.get("is_done")
    task.is_done = bool(is_done) if is_done is not None else (not task.is_done)
    task.save(update_fields=["is_done"])
    return {"task_id": task.id, "is_done": task.is_done}


@agent_tool(STUDENT_AGENT)
def explain_topic(user, **params):
    """AI tutor: explain a topic in plain language, grounded in the course's
    uploaded materials when available. This is a *read* action (it never
    changes any data) but it runs through the Student Agent rather than the
    read-only Chatbot because it does real reasoning over material content,
    not canned lookups."""
    if not user.is_student:
        raise ToolPermissionError("Only students can call explain_topic.")
    topic = Topic.objects.filter(id=params.get("topic_id")).first()
    if not topic:
        raise ToolValidationError("Topic not found.")
    if not topic.course.students.filter(id=user.id).exists():
        raise ToolPermissionError("You are not enrolled in this topic's course.")

    # Only ground the explanation in materials that actually mention this
    # topic -- not just any material uploaded to the course -- so a student
    # asking about one topic never gets an explanation built from a
    # different topic's content (see courses.services.get_relevant_materials).
    relevant_materials = course_services.get_relevant_materials(topic.course, topic.name, limit=3)
    context_text = "\n\n".join(m.extracted_text for m in relevant_materials)
    if not context_text.strip():
        # No uploaded material covers this specific topic -- rather than
        # telling the student "no material", let the AI explain the topic
        # itself from general knowledge (same fallback pattern already used
        # by generate_practice_quiz below).
        context_text = ai_generation.synthesize_topic_primer(topic.name, topic.course.title)
    mastery = TopicMastery.objects.filter(student=user, topic=topic).first()
    explanation = ai_generation.explain_topic(
        topic.name, topic.course.title, context_text,
        mastery.mastery_percent if mastery else None,
    )
    return {"topic": topic.name, "explanation": explanation}


@agent_tool(STUDENT_AGENT)
def generate_practice_quiz(user, **params):
    """AI-generate a short self-practice quiz (with answers revealed
    immediately) for a practice task's topic, so the student can drill the
    exact thing they're supposed to be strengthening. This is deliberately
    NOT a real graded Quiz row -- only instructors can create those (RBAC) --
    it's ephemeral self-check content returned straight in the tool result,
    same pattern as explain_topic."""
    if not user.is_student:
        raise ToolPermissionError("Only students can call generate_practice_quiz.")

    task = None
    if params.get("task_id"):
        task = PracticeTask.objects.filter(id=params.get("task_id"), plan__student=user).select_related("topic").first()
        if not task:
            raise ToolValidationError("Practice task not found or does not belong to you.")
        topic = task.topic
    else:
        topic = Topic.objects.filter(id=params.get("topic_id")).first()
        if not topic:
            raise ToolValidationError("Topic not found.")
        if not topic.course.students.filter(id=user.id).exists():
            raise ToolPermissionError("You are not enrolled in this topic's course.")

    # Same topic-relevance filtering as explain_topic -- practice questions
    # should be drilling the requested topic, not whatever material happens
    # to be first in the course.
    relevant_materials = course_services.get_relevant_materials(topic.course, topic.name, limit=3)
    context_text = "\n\n".join(m.extracted_text for m in relevant_materials)
    if not context_text.strip():
        context_text = ai_generation.synthesize_topic_primer(topic.name, topic.course.title)

    num_mcq = int(params.get("num_mcq", 4) or 4)
    questions = ai_generation.generate_quiz_questions(context_text, num_mcq=num_mcq, num_essay=0)
    if not questions:
        raise ToolValidationError("Could not generate practice questions for this topic yet.")

    # Reveal the correct answer inline (unlike a real quiz) -- the point
    # here is self-check practice, not assessment.
    practice_items = [
        {
            "question": q["text"],
            "choices": [c["text"] for c in q.get("choices", [])],
            "correct_answer": next((c["text"] for c in q.get("choices", []) if c.get("is_correct")), None),
        }
        for q in questions if q.get("type") == "mcq"
    ]
    return {"topic": topic.name, "task_id": task.id if task else None, "practice_questions": practice_items}


@agent_tool(STUDENT_AGENT)
def summarize_material(user, **params):
    """AI-summarize a course Material in whatever format the student picks
    (bullet points, detailed paragraphs, TL;DR, exam-prep Q&A, or ELI5) --
    not limited to the single fixed-format summary an instructor can
    generate with generate_summary_from_material. Deliberately read-only and
    ephemeral (like explain_topic/generate_practice_quiz): it never
    overwrites Material.ai_summary, which stays the instructor's own cached
    summary shown to every student."""
    if not user.is_student:
        raise ToolPermissionError("Only students can call summarize_material.")
    material = Material.objects.filter(id=params.get("material_id")).first()
    if not material:
        raise ToolValidationError("Material not found.")
    if not material.course.students.filter(id=user.id).exists():
        raise ToolPermissionError("You are not enrolled in this material's course.")
    if material.kind == Material.Kind.VIDEO:
        raise ToolValidationError("AI summaries are not generated for video lectures.")
    if material.extraction_status != Material.ExtractionStatus.DONE:
        raise ToolValidationError(f"This file's text could not be read (status: {material.extraction_status}).")

    style = params.get("style") or ai_generation.DEFAULT_SUMMARY_STYLE
    if style not in ai_generation.SUMMARY_STYLES:
        raise ToolValidationError(
            f"style must be one of: {', '.join(ai_generation.SUMMARY_STYLES)}."
        )

    summary = ai_generation.summarize(material.extracted_text, material.course.title, material.title, style=style)
    return {"material_id": material.id, "material": material.title, "style": style, "summary": summary}


_TUTOR_MODES = {"ask", "explain_simply", "give_example", "explain_paragraph", "hint", "quiz_me"}


@agent_tool(STUDENT_AGENT)
def ask_about_material(user, **params):
    """AI Tutor scoped to ONE specific lecture/material -- "AI Tutor built
    on the lecture". Unlike explain_topic (which is keyed to a Topic and
    searches the whole course for relevant material), this always answers
    using exactly the material the student is looking at, in one of several
    modes: a free question ('ask'), 'explain_simply', 'give_example',
    'explain_paragraph' (pass the passage in `question`), 'hint' (pass what
    the student is stuck on in `question`), or 'quiz_me' -- which reuses the
    same practice_questions shape as generate_practice_quiz so the UI can
    render it identically."""
    if not user.is_student:
        raise ToolPermissionError("Only students can call ask_about_material.")
    material = Material.objects.filter(id=params.get("material_id")).first()
    if not material:
        raise ToolValidationError("Material not found.")
    if not material.course.students.filter(id=user.id).exists():
        raise ToolPermissionError("You are not enrolled in this material's course.")
    if material.kind == Material.Kind.VIDEO:
        raise ToolValidationError("The AI tutor reads uploaded documents, not video lectures.")
    if material.extraction_status != Material.ExtractionStatus.DONE:
        raise ToolValidationError(f"This file's text could not be read (status: {material.extraction_status}).")

    mode = params.get("mode") or "ask"
    if mode not in _TUTOR_MODES:
        raise ToolValidationError(f"mode must be one of: {', '.join(sorted(_TUTOR_MODES))}.")
    question = (params.get("question") or "").strip()

    if mode == "quiz_me":
        num_mcq = int(params.get("num_mcq") or 3)
        questions = ai_generation.generate_quiz_questions(material.extracted_text, num_mcq=num_mcq, num_essay=0)
        practice_items = [
            {
                "question": q["text"],
                "choices": [c["text"] for c in q.get("choices", [])],
                "correct_answer": next((c["text"] for c in q.get("choices", []) if c.get("is_correct")), None),
            }
            for q in questions if q.get("type") == "mcq"
        ]
        if not practice_items:
            raise ToolValidationError("Could not generate questions from this lecture yet.")
        return {"material": material.title, "topic": material.title, "practice_questions": practice_items}

    if mode == "explain_paragraph" and not question:
        raise ToolValidationError("Paste the paragraph you want explained in 'question'.")
    if mode == "hint" and not question:
        raise ToolValidationError("Describe what you're stuck on in 'question'.")

    reply = ai_generation.tutor_on_material(
        material.extracted_text, material.course.title, material.title, mode,
        question=question, paragraph=question if mode == "explain_paragraph" else "",
    )
    return {"material_id": material.id, "material": material.title, "mode": mode, "tutor_reply": reply}


@agent_tool(STUDENT_AGENT)
def mark_material_done(user, **params):
    """Toggle whether a Material is marked as studied -- powers the
    "Completed Materials" count on the Learning Analytics dashboard and the
    study-streak calculation (courses/services.py:compute_learning_dashboard).
    Not destructive: freely reversible, no confirmation needed."""
    if not user.is_student:
        raise ToolPermissionError("Only students can call mark_material_done.")
    material = Material.objects.filter(id=params.get("material_id")).first()
    if not material:
        raise ToolValidationError("Material not found.")
    if not material.course.students.filter(id=user.id).exists():
        raise ToolPermissionError("You are not enrolled in this material's course.")

    is_done = params.get("is_done", True)
    is_done = is_done if isinstance(is_done, bool) else str(is_done).lower() not in ("false", "0", "")
    progress, _ = MaterialProgress.objects.get_or_create(student=user, material=material)
    progress.is_done = is_done
    progress.done_at = timezone.now() if is_done else None
    progress.save(update_fields=["is_done", "done_at"])
    return {"material_id": material.id, "material": material.title, "is_done": is_done}


@agent_tool(STUDENT_AGENT)
def get_learning_dashboard(user, **params):
    """Learning Analytics Dashboard: overall progress, quiz average,
    strongest/weakest topic, materials completed, study streak, plus a
    one-paragraph AI narrative generated from those same numbers (so the
    prose can never contradict the stats). See
    courses/services.py:compute_learning_dashboard."""
    if not user.is_student:
        raise ToolPermissionError("Only students can call get_learning_dashboard.")
    stats = course_services.compute_learning_dashboard(user)
    facts = (
        f"Overall progress: {stats['overall_progress_percent']}%. Quiz average: {stats['quiz_average']}%. "
        f"Strongest topic: {stats['strongest_topic'] or 'not enough data'}. "
        f"Weakest topic: {stats['weakest_topic'] or 'not enough data'}. "
        f"Completed {stats['completed_materials']} of {stats['total_materials']} materials. "
        f"Current study streak: {stats['study_streak_days']} day(s)."
    )
    stats["insight"] = ai_generation.narrate_student_progress(facts)
    return stats


@agent_tool(STUDENT_AGENT)
def get_recommendations(user, **params):
    """AI Learning Recommendations: a short, ranked "Recommended for you"
    list, each with a concrete reason (a score, a wrong-answer count, or
    "never practiced"). Rule-based -- see
    courses/services.py:compute_recommendations for why an LLM call isn't
    used here."""
    if not user.is_student:
        raise ToolPermissionError("Only students can call get_recommendations.")
    return {"recommendations": course_services.compute_recommendations(user)}


def _adaptive_difficulty_for_mastery(mastery_percent):
    if mastery_percent is None:
        return AdaptivePracticeSession.Difficulty.MEDIUM
    if mastery_percent < 50:
        return AdaptivePracticeSession.Difficulty.EASY
    if mastery_percent < 80:
        return AdaptivePracticeSession.Difficulty.MEDIUM
    return AdaptivePracticeSession.Difficulty.HARD


_DIFFICULTY_ORDER = ["easy", "medium", "hard"]


def _strip_answers(choices):
    return [c["text"] for c in choices]


@agent_tool(STUDENT_AGENT)
def start_adaptive_practice(user, **params):
    """AI-Generated Adaptive Practice Mode, step 1: check the student's
    history (current mastery on the topic) to pick a starting difficulty,
    then generate one question at that level. Ends any previous active
    practice session for this student first, so there is only ever one
    live question in flight."""
    if not user.is_student:
        raise ToolPermissionError("Only students can call start_adaptive_practice.")
    topic = Topic.objects.filter(id=params.get("topic_id")).select_related("course").first()
    if not topic:
        raise ToolValidationError("Topic not found.")
    if not topic.course.students.filter(id=user.id).exists():
        raise ToolPermissionError("You are not enrolled in this topic's course.")

    AdaptivePracticeSession.objects.filter(student=user, is_active=True).update(is_active=False)

    mastery = TopicMastery.objects.filter(student=user, topic=topic).first()
    difficulty = _adaptive_difficulty_for_mastery(mastery.mastery_percent if mastery else None)

    relevant_materials = course_services.get_relevant_materials(topic.course, topic.name, limit=3)
    context_text = "\n\n".join(m.extracted_text for m in relevant_materials)
    if not context_text.strip():
        context_text = ai_generation.synthesize_topic_primer(topic.name, topic.course.title)

    question = ai_generation.generate_single_question(context_text, difficulty)
    if not question:
        raise ToolValidationError("Could not generate a practice question for this topic yet.")

    session = AdaptivePracticeSession.objects.create(
        student=user, topic=topic, context_text=context_text, difficulty=difficulty,
        current_question_text=question["text"], current_choices=question["choices"],
    )
    return {
        "session_id": session.id, "topic": topic.name, "difficulty": difficulty,
        "question": question["text"], "choices": _strip_answers(question["choices"]),
        "question_number": 1,
    }


@agent_tool(STUDENT_AGENT)
def submit_adaptive_answer(user, **params):
    """AI-Generated Adaptive Practice Mode, step 2: grade the selected
    choice against the active session's current question, adjust
    difficulty (two in a row correct -> harder; one wrong -> easier, with
    the correct answer shown), then immediately generate the next question
    at the new difficulty."""
    if not user.is_student:
        raise ToolPermissionError("Only students can call submit_adaptive_answer.")
    session = AdaptivePracticeSession.objects.filter(student=user, is_active=True).select_related("topic").first()
    if not session:
        raise ToolValidationError("No active practice session. Start one first.")

    try:
        selected_index = int(params.get("selected_index"))
    except (TypeError, ValueError):
        raise ToolValidationError("selected_index is required.")
    choices = session.current_choices or []
    if not (0 <= selected_index < len(choices)):
        raise ToolValidationError("selected_index is out of range for the current question.")

    was_correct = bool(choices[selected_index].get("is_correct"))
    correct_answer = next((c["text"] for c in choices if c.get("is_correct")), None)

    session.questions_asked += 1
    idx = _DIFFICULTY_ORDER.index(session.difficulty)
    if was_correct:
        session.questions_correct += 1
        session.streak_correct += 1
        session.streak_wrong = 0
        if session.streak_correct >= 2 and idx < len(_DIFFICULTY_ORDER) - 1:
            idx += 1
            session.streak_correct = 0
        feedback = "✅ Correct!"
    else:
        session.streak_wrong += 1
        session.streak_correct = 0
        if idx > 0:
            idx -= 1
        feedback = f"❌ Not quite. The correct answer was: {correct_answer}."
    session.difficulty = _DIFFICULTY_ORDER[idx]

    next_question = ai_generation.generate_single_question(session.context_text, session.difficulty)
    if not next_question:
        session.is_active = False
        session.save()
        return {
            "topic": session.topic.name, "feedback": feedback, "was_correct": was_correct,
            "correct_answer": correct_answer, "score": f"{session.questions_correct}/{session.questions_asked}",
            "ended": True,
        }

    session.current_question_text = next_question["text"]
    session.current_choices = next_question["choices"]
    session.save()

    return {
        "topic": session.topic.name, "feedback": feedback, "was_correct": was_correct,
        "correct_answer": correct_answer, "difficulty": session.difficulty,
        "next_question": next_question["text"], "next_choices": _strip_answers(next_question["choices"]),
        "question_number": session.questions_asked + 1,
        "score": f"{session.questions_correct}/{session.questions_asked}",
    }


@agent_tool(STUDENT_AGENT)
def end_adaptive_practice(user, **params):
    """End the active adaptive-practice session and return a short summary.
    Freely reversible (a new session can always be started), so no
    confirmation is required."""
    if not user.is_student:
        raise ToolPermissionError("Only students can call end_adaptive_practice.")
    session = AdaptivePracticeSession.objects.filter(student=user, is_active=True).select_related("topic").first()
    if not session:
        raise ToolValidationError("No active practice session to end.")
    session.is_active = False
    session.save(update_fields=["is_active"])
    return {
        "topic": session.topic.name, "questions_asked": session.questions_asked,
        "questions_correct": session.questions_correct, "final_difficulty": session.difficulty,
    }


# --------------------------------------------------------------------------
# Instructor Agent tools
# --------------------------------------------------------------------------

@agent_tool(INSTRUCTOR_AGENT)
def get_course_analytics(user, **params):
    if not user.is_instructor:
        raise ToolPermissionError("Only instructors can call get_course_analytics.")
    course = Course.objects.filter(id=params.get("course_id"), instructor=user).first()
    if not course:
        raise ToolValidationError("Course not found or you do not own it.")

    attempts = QuizAttempt.objects.filter(quiz__course=course, completed_at__isnull=False)
    avg_score = attempts.aggregate(avg=Avg("score"))["avg"]
    topic_breakdown = [
        {
            "topic": tm["topic__name"],
            "avg_mastery": round(tm["avg"] or 0, 1),
        }
        for tm in TopicMastery.objects.filter(topic__course=course)
        .values("topic__name")
        .annotate(avg=Avg("mastery_percent"))
    ]

    # Score distribution buckets, for a histogram on the analytics page.
    buckets = [{"label": "0-49%", "count": 0}, {"label": "50-69%", "count": 0},
               {"label": "70-89%", "count": 0}, {"label": "90-100%", "count": 0}]
    for a in attempts:
        if a.score is None:
            continue
        idx = 0 if a.score < 50 else 1 if a.score < 70 else 2 if a.score < 90 else 3
        buckets[idx]["count"] += 1

    recent_attempts = [
        {"student": a.student.username, "quiz": a.quiz.title, "score": a.score,
         "completed_at": a.completed_at.isoformat() if a.completed_at else None}
        for a in attempts.select_related("student", "quiz").order_by("-completed_at")[:15]
    ]

    pending_essay_count = QuestionResponse.objects.filter(
        needs_review=True, attempt__quiz__course=course,
    ).count()

    return {
        "course": course.title,
        "student_count": course.students.count(),
        "quiz_count": course.quizzes.count(),
        "attempt_count": attempts.count(),
        "avg_score": round(avg_score, 1) if avg_score is not None else None,
        "topic_breakdown": topic_breakdown,
        "score_distribution": buckets,
        "recent_attempts": recent_attempts,
        "pending_essay_count": pending_essay_count,
        "ai_summary": course.ai_summary,
        "ai_summary_generated_at": course.ai_summary_generated_at.isoformat() if course.ai_summary_generated_at else None,
    }


@agent_tool(INSTRUCTOR_AGENT)
def get_student_performance(user, **params):
    if not user.is_instructor:
        raise ToolPermissionError("Only instructors can call get_student_performance.")
    course = Course.objects.filter(id=params.get("course_id"), instructor=user).first()
    if not course:
        raise ToolValidationError("Course not found or you do not own it.")
    student = course.students.filter(id=params.get("student_id")).first()
    if not student:
        raise ToolValidationError("Student not found in this course.")

    records = TopicMastery.objects.filter(student=student, topic__course=course)
    attempts = (
        QuizAttempt.objects.filter(student=student, quiz__course=course, completed_at__isnull=False)
        .select_related("quiz").order_by("completed_at")
    )
    return {
        "student": student.username,
        "student_id": student.id,
        "attempt_history": [
            {"quiz": a.quiz.title, "score": a.score, "completed_at": a.completed_at.isoformat()}
            for a in attempts
        ],
        "topics": [
            {"topic": r.topic.name, "mastery_percent": round(r.mastery_percent, 1)}
            for r in records
        ],
    }


@agent_tool(INSTRUCTOR_AGENT)
def create_quiz(user, **params):
    if not user.is_instructor:
        raise ToolPermissionError("Only instructors can call create_quiz.")
    course = Course.objects.filter(id=params.get("course_id"), instructor=user).first()
    if not course:
        raise ToolValidationError("Course not found or you do not own it.")
    topic = Topic.objects.filter(id=params.get("topic_id"), course=course).first()
    if not topic:
        raise ToolValidationError("Topic not found in this course.")
    title = params.get("title") or f"{topic.name} Quiz"
    quiz = Quiz.objects.create(
        course=course, topic=topic, title=title,
        created_by=user, created_via_agent=True,
    )
    return {"quiz_id": quiz.id, "title": quiz.title, "topic": topic.name}


@agent_tool(INSTRUCTOR_AGENT)
def generate_course_summary(user, **params):
    if not user.is_instructor:
        raise ToolPermissionError("Only instructors can call generate_course_summary.")
    course = Course.objects.filter(id=params.get("course_id"), instructor=user).first()
    if not course:
        raise ToolValidationError("Course not found or you do not own it.")

    module_count = course.modules.count()
    lesson_count = sum(m.lessons.count() for m in course.modules.all())
    weakest = (
        TopicMastery.objects.filter(topic__course=course)
        .values("topic__name")
        .annotate(avg=Avg("mastery_percent"))
        .order_by("avg")[:3]
    )
    weak_text = ", ".join(f"{t['topic__name']} ({t['avg']:.0f}%)" for t in weakest) or "not enough data yet"
    facts = (
        f"{course.title} has {course.students.count()} students across {module_count} modules "
        f"and {lesson_count} lessons. Weakest topics class-wide: {weak_text}."
    )
    # Layer an AI-authored narrative summary on top of the hard facts when
    # possible; ai_generation falls back to a deterministic summary if the
    # LLM is unavailable, so this never fails outright.
    summary_text = ai_generation.narrate_course_summary(course.title, facts)

    course.ai_summary = summary_text
    course.ai_summary_generated_at = timezone.now()
    course.save(update_fields=["ai_summary", "ai_summary_generated_at"])
    return {"course": course.title, "summary": summary_text}


@agent_tool(INSTRUCTOR_AGENT, destructive=True)
def cancel_quiz(user, **params):
    if not user.is_instructor:
        raise ToolPermissionError("Only instructors can call cancel_quiz.")
    quiz = Quiz.objects.filter(id=params.get("quiz_id"), course__instructor=user).first()
    if not quiz:
        raise ToolValidationError("Quiz not found or you do not own it.")
    quiz.is_active = False
    quiz.cancelled_at = timezone.now()
    quiz.save(update_fields=["is_active", "cancelled_at"])
    return {"quiz_id": quiz.id, "title": quiz.title, "is_active": quiz.is_active}


@agent_tool(INSTRUCTOR_AGENT)
def review_study_plan(user, **params):
    """approve / reject a pending StudyPlan -- instructor-only per RBAC matrix."""
    if not user.is_instructor:
        raise ToolPermissionError("Only instructors can review study plans.")
    plan = StudyPlan.objects.filter(id=params.get("plan_id")).first()
    if not plan:
        raise ToolValidationError("Study plan not found.")
    if not plan.student.courses_enrolled.filter(instructor=user).exists():
        raise ToolPermissionError("This student is not in any of your courses.")

    decision = params.get("decision")
    if decision not in ("approved", "rejected"):
        raise ToolValidationError("decision must be 'approved' or 'rejected'.")

    plan.status = decision
    plan.review_note = params.get("note", "")
    plan.reviewed_by = user
    plan.reviewed_at = timezone.now()
    plan.save(update_fields=["status", "review_note", "reviewed_by", "reviewed_at"])

    note_suffix = f" Note: {plan.review_note}" if plan.review_note else ""
    notify(plan.student, f"Your study plan '{plan.title}' was {decision} by {user.username}.{note_suffix}")

    return {"plan_id": plan.id, "status": plan.status}


@agent_tool(INSTRUCTOR_AGENT)
def get_at_risk_students(user, **params):
    """Flag students whose average mastery across a course is below a
    threshold (default 50%), so the instructor can proactively intervene
    instead of only reacting to a student asking for help."""
    if not user.is_instructor:
        raise ToolPermissionError("Only instructors can call get_at_risk_students.")
    course = Course.objects.filter(id=params.get("course_id"), instructor=user).first()
    if not course:
        raise ToolValidationError("Course not found or you do not own it.")
    threshold = float(params.get("threshold", 50) or 50)

    at_risk = []
    for student in course.students.all():
        records = list(TopicMastery.objects.filter(student=student, topic__course=course))
        if not records:
            at_risk.append({"student_id": student.id, "student": student.username, "avg_mastery": None, "reason": "no quiz attempts yet"})
            continue
        avg = sum(r.mastery_percent for r in records) / len(records)
        if avg < threshold:
            weakest = min(records, key=lambda r: r.mastery_percent)
            at_risk.append({
                "student_id": student.id, "student": student.username,
                "avg_mastery": round(avg, 1), "weakest_topic": weakest.topic.name,
                "reason": f"average mastery {avg:.0f}% is below {threshold:.0f}%",
            })
    return {"course": course.title, "threshold": threshold, "at_risk_students": at_risk}


@agent_tool(INSTRUCTOR_AGENT)
def send_announcement(user, **params):
    """Broadcast a notification to every student enrolled in a course the
    instructor owns -- e.g. 'new material posted', 'exam moved to Thursday'."""
    if not user.is_instructor:
        raise ToolPermissionError("Only instructors can call send_announcement.")
    course = Course.objects.filter(id=params.get("course_id"), instructor=user).first()
    if not course:
        raise ToolValidationError("Course not found or you do not own it.")
    message = (params.get("message") or "").strip()
    if not message:
        raise ToolValidationError("Announcement message is required.")

    students = list(course.students.all())
    notify_many(students, f"[{course.title}] {message}")
    return {"course": course.title, "recipient_count": len(students), "message": message}


# --------------------------------------------------------------------------
# Instructor Agent tools -- materials, AI generation, manual authoring
# --------------------------------------------------------------------------

@agent_tool(INSTRUCTOR_AGENT)
def upload_material(user, **params):
    """Create a Material record for an already-saved uploaded file and run
    text extraction on it. The view is responsible for actually validating
    request.FILES and calling this with a saved Django File object."""
    if not user.is_instructor:
        raise ToolPermissionError("Only instructors can upload materials.")
    course = Course.objects.filter(id=params.get("course_id"), instructor=user).first()
    if not course:
        raise ToolValidationError("Course not found or you do not own it.")

    django_file = params.get("django_file")
    if not django_file:
        raise ToolValidationError("No file provided.")

    module = Module.objects.filter(id=params.get("module_id"), course=course).first() if params.get("module_id") else None
    lesson = None
    if params.get("lesson_id") and module:
        lesson = module.lessons.filter(id=params.get("lesson_id")).first()

    kind = text_extraction.guess_kind(django_file.name)
    material = Material.objects.create(
        course=course, module=module, lesson=lesson,
        title=params.get("title") or django_file.name,
        file=django_file, kind=kind, uploaded_by=user,
    )
    text, status = text_extraction.extract_text(material.file, kind)
    material.extracted_text = text
    material.extraction_status = status
    material.save(update_fields=["extracted_text", "extraction_status"])

    return {
        "material_id": material.id, "title": material.title, "kind": material.kind,
        "extraction_status": material.extraction_status, "chars_extracted": len(text),
    }


@agent_tool(INSTRUCTOR_AGENT)
def generate_summary_from_material(user, **params):
    """AI reads an uploaded Material's extracted text and produces a summary."""
    if not user.is_instructor:
        raise ToolPermissionError("Only instructors can generate summaries.")
    material = Material.objects.filter(id=params.get("material_id"), course__instructor=user).first()
    if not material:
        raise ToolValidationError("Material not found or you do not own its course.")
    if material.kind == Material.Kind.VIDEO:
        raise ToolValidationError("AI summaries are not generated for video lectures.")
    if material.extraction_status != Material.ExtractionStatus.DONE:
        raise ToolValidationError(f"This file's text could not be read (status: {material.extraction_status}).")

    summary = ai_generation.summarize(material.extracted_text, material.course.title, material.title)
    material.ai_summary = summary
    material.ai_summary_generated_at = timezone.now()
    material.save(update_fields=["ai_summary", "ai_summary_generated_at"])
    return {"material_id": material.id, "summary": summary}


@agent_tool(INSTRUCTOR_AGENT)
def generate_quiz_from_material(user, **params):
    """AI reads an uploaded Material and generates a quiz: MCQ questions are
    ready to auto-grade immediately; essay questions are included too but
    always require the instructor's manual grading once answered."""
    if not user.is_instructor:
        raise ToolPermissionError("Only instructors can generate quizzes.")
    material = Material.objects.filter(id=params.get("material_id"), course__instructor=user).first()
    if not material:
        raise ToolValidationError("Material not found or you do not own its course.")
    if material.kind == Material.Kind.VIDEO:
        raise ToolValidationError("AI quiz generation is not available for video lectures.")
    if material.extraction_status != Material.ExtractionStatus.DONE:
        raise ToolValidationError(f"This file's text could not be read (status: {material.extraction_status}).")

    topic = Topic.objects.filter(id=params.get("topic_id"), course=material.course).first()
    if not topic:
        raise ToolValidationError("Topic not found in this course.")

    num_mcq, num_essay = _clean_question_counts(params)
    generated = ai_generation.generate_quiz_questions(material.extracted_text, num_mcq, num_essay)
    if not generated:
        raise ToolValidationError("AI could not generate questions from this material.")

    quiz = _build_quiz_from_generated(
        course=material.course, topic=topic, user=user, generated=generated,
        title=params.get("title") or f"AI Quiz: {material.title}",
        generated_from_material=material,
    )
    return {
        "quiz_id": quiz.id, "title": quiz.title,
        "mcq_count": sum(1 for q in generated if q.get("type") != "essay"),
        "essay_count": sum(1 for q in generated if q.get("type") == "essay"),
    }


@agent_tool(INSTRUCTOR_AGENT)
def generate_quiz_for_topic(user, **params):
    """AI generates a quiz for a topic WITHOUT needing one specific Material
    picked first -- it draws on every already-processed Material in the
    course as context (falling back to general knowledge of the topic name
    if the course has no readable materials yet). This is the tool to use
    whenever the instructor asks for "a recommended/AI quiz on topic X" in
    general, rather than always going through add_question_to_quiz by hand
    (which tends to default to essay-only since there's no material to
    ground multiple-choice questions in)."""
    if not user.is_instructor:
        raise ToolPermissionError("Only instructors can generate quizzes.")
    course = Course.objects.filter(id=params.get("course_id"), instructor=user).first()
    if not course:
        raise ToolValidationError("Course not found or you do not own it.")
    topic = Topic.objects.filter(id=params.get("topic_id"), course=course).first()
    if not topic:
        raise ToolValidationError("Topic not found in this course.")

    # Ground the quiz in materials that actually mention this topic, not
    # just any material in the course (same fix as explain_topic /
    # generate_practice_quiz -- see courses.services.get_relevant_materials).
    relevant_materials = course_services.get_relevant_materials(course, topic.name, limit=5)
    context_text = "\n\n".join(m.extracted_text for m in relevant_materials)
    used_material_context = bool(context_text.strip())
    if not used_material_context:
        # No readable material yet -- fall back to a short synthetic
        # "textbook-style" passage about the topic so the MCQ generator
        # (which needs sentences to blank out) still has something to work
        # with, instead of forcing essay-only questions by default.
        context_text = ai_generation.synthesize_topic_primer(topic.name, course.title)

    num_mcq, num_essay = _clean_question_counts(params)
    generated = ai_generation.generate_quiz_questions(context_text, num_mcq, num_essay)
    if not generated:
        raise ToolValidationError("AI could not generate questions for this topic yet -- try uploading a material first.")

    quiz = _build_quiz_from_generated(
        course=course, topic=topic, user=user, generated=generated,
        title=params.get("title") or f"AI Quiz: {topic.name}",
    )
    return {
        "quiz_id": quiz.id, "title": quiz.title,
        "mcq_count": sum(1 for q in generated if q.get("type") != "essay"),
        "essay_count": sum(1 for q in generated if q.get("type") == "essay"),
        "used_material_context": used_material_context,
    }


def _clean_question_counts(params):
    num_mcq = int(params.get("num_mcq", 5) or 5)
    num_essay = int(params.get("num_essay", 0) or 0)
    if num_mcq + num_essay < 1 or num_mcq + num_essay > 20:
        raise ToolValidationError("Total question count must be between 1 and 20.")
    return num_mcq, num_essay


def _build_quiz_from_generated(course, topic, user, generated, title, generated_from_material=None):
    quiz = Quiz.objects.create(
        course=course, topic=topic, title=title,
        created_by=user, created_via_agent=True, generated_from_material=generated_from_material,
    )
    for i, q in enumerate(generated, start=1):
        question = Question.objects.create(
            quiz=quiz, topic=topic, text=q["text"], order=i, generated_by_ai=True,
            question_type=Question.Type.ESSAY if q.get("type") == "essay" else Question.Type.MCQ,
        )
        if question.question_type == Question.Type.MCQ:
            for c in q.get("choices", []):
                Choice.objects.create(question=question, text=c.get("text", ""), is_correct=bool(c.get("is_correct")))
    return quiz


@agent_tool(INSTRUCTOR_AGENT)
def analyze_class_performance(user, **params):
    """Instructor AI Insights: hardest topic, class average score, at-risk
    count, and an AI-written recommended action -- the instructor-facing
    counterpart of the student's get_learning_dashboard. See
    courses/services.py:compute_class_performance for the numbers."""
    if not user.is_instructor:
        raise ToolPermissionError("Only instructors can call analyze_class_performance.")
    course = Course.objects.filter(id=params.get("course_id"), instructor=user).first()
    if not course:
        raise ToolValidationError("Course not found or you do not own it.")

    stats = course_services.compute_class_performance(course)
    facts = (
        f"Hardest topic: {stats['most_difficult_topic'] or 'not enough data yet'} "
        f"(avg mastery {stats['most_difficult_topic_avg_mastery']}%). "
        f"Class average quiz score: {stats['average_score']}%. "
        f"Students at risk: {stats['at_risk_count']} of {stats['student_count']}."
    )
    stats["insight"] = ai_generation.narrate_class_insight(course.title, facts)
    return stats


@agent_tool(INSTRUCTOR_AGENT)
def generate_remedial_quiz(user, **params):
    """One-step follow-up to analyze_class_performance: AI-generate a quiz
    for the course's current hardest topic (by average mastery), or an
    explicitly given topic_id. Reuses the same generation/grounding as
    generate_quiz_for_topic."""
    if not user.is_instructor:
        raise ToolPermissionError("Only instructors can generate quizzes.")
    course = Course.objects.filter(id=params.get("course_id"), instructor=user).first()
    if not course:
        raise ToolValidationError("Course not found or you do not own it.")

    topic_id = params.get("topic_id")
    if topic_id:
        topic = Topic.objects.filter(id=topic_id, course=course).first()
        if not topic:
            raise ToolValidationError("Topic not found in this course.")
    else:
        stats = course_services.compute_class_performance(course)
        topic = Topic.objects.filter(id=stats.get("most_difficult_topic_id"), course=course).first()
        if not topic:
            raise ToolValidationError(
                "Not enough quiz history yet to identify the hardest topic -- pass topic_id explicitly."
            )

    relevant_materials = course_services.get_relevant_materials(course, topic.name, limit=5)
    context_text = "\n\n".join(m.extracted_text for m in relevant_materials)
    if not context_text.strip():
        context_text = ai_generation.synthesize_topic_primer(topic.name, course.title)

    num_mcq, num_essay = _clean_question_counts(params)
    generated = ai_generation.generate_quiz_questions(context_text, num_mcq, num_essay)
    if not generated:
        raise ToolValidationError("AI could not generate remedial questions for this topic yet.")

    quiz = _build_quiz_from_generated(
        course=course, topic=topic, user=user, generated=generated,
        title=params.get("title") or f"Remedial: {topic.name}",
    )
    return {
        "quiz_id": quiz.id, "title": quiz.title, "topic": topic.name,
        "mcq_count": sum(1 for q in generated if q.get("type") != "essay"),
        "essay_count": sum(1 for q in generated if q.get("type") == "essay"),
    }


@agent_tool(INSTRUCTOR_AGENT)
def add_question_to_quiz(user, **params):
    """Manually author a single question (mcq or essay) on an existing quiz."""
    if not user.is_instructor:
        raise ToolPermissionError("Only instructors can add questions.")
    quiz = Quiz.objects.filter(id=params.get("quiz_id"), course__instructor=user).first()
    if not quiz:
        raise ToolValidationError("Quiz not found or you do not own it.")
    text = (params.get("text") or "").strip()
    if not text:
        raise ToolValidationError("Question text is required.")
    q_type = params.get("question_type", "mcq")
    if q_type not in (Question.Type.MCQ, Question.Type.ESSAY):
        raise ToolValidationError("question_type must be 'mcq' or 'essay'.")

    order = quiz.questions.count() + 1
    question = Question.objects.create(quiz=quiz, topic=quiz.topic, text=text, question_type=q_type, order=order)

    if q_type == Question.Type.MCQ:
        choices = params.get("choices") or []
        if len(choices) < 2 or not any(c.get("is_correct") for c in choices):
            question.delete()
            raise ToolValidationError("MCQ needs at least 2 choices and exactly one marked correct.")
        for c in choices:
            Choice.objects.create(question=question, text=c.get("text", ""), is_correct=bool(c.get("is_correct")))

    return {"question_id": question.id, "quiz_id": quiz.id, "question_type": question.question_type}


@agent_tool(INSTRUCTOR_AGENT)
def grade_essay_response(user, **params):
    """Manually grade one essay/short-answer response. score must be 0-1."""
    if not user.is_instructor:
        raise ToolPermissionError("Only instructors can grade essay responses.")
    response = QuestionResponse.objects.filter(
        id=params.get("response_id"), attempt__quiz__course__instructor=user,
    ).select_related("attempt", "question").first()
    if not response:
        raise ToolValidationError("Response not found or you do not own its course.")

    try:
        score = float(params.get("score"))
    except (TypeError, ValueError):
        raise ToolValidationError("score must be a number between 0 and 1.")
    if not (0 <= score <= 1):
        raise ToolValidationError("score must be between 0 and 1.")

    attempt = course_services.grade_essay_response(response, score, params.get("feedback", ""), user)

    notify(
        attempt.student,
        f"Your answer on '{attempt.quiz.title}' was graded ({round(score * 100)}%)." +
        (f" Feedback: {params.get('feedback')}" if params.get("feedback") else ""),
    )

    return {"response_id": response.id, "attempt_id": attempt.id, "attempt_status": attempt.status, "attempt_score": attempt.score}


@agent_tool(INSTRUCTOR_AGENT, destructive=True)
def delete_material(user, **params):
    """Permanently delete an uploaded Material (lecture video or course
    document), including its file on disk. Destructive: requires confirm=true."""
    if not user.is_instructor:
        raise ToolPermissionError("Only instructors can delete materials.")
    material = Material.objects.filter(id=params.get("material_id"), course__instructor=user).first()
    if not material:
        raise ToolValidationError("Material not found or you do not own its course.")

    material_id, title, kind = material.id, material.title, material.kind
    generated_quiz_count = material.generated_quizzes.count()
    if material.file:
        material.file.delete(save=False)
    material.delete()
    return {
        "deleted_material_id": material_id, "title": title, "kind": kind,
        "note": (
            f"{generated_quiz_count} quiz(zes) generated from this material were kept (they are independent records)."
            if generated_quiz_count else None
        ),
    }
