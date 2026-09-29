"""Domain logic that is NOT agent-tool-gated: grading a quiz attempt and
rolling the result into TopicMastery. MCQ responses are graded immediately;
essay/short-answer responses are flagged needs_review and wait for an
instructor to grade them (agents/tools.py:grade_essay_response), matching
the requirement that only multiple-choice is fully automatic."""
import re
from datetime import timedelta

from django.db.models import Avg
from django.utils import timezone
from .models import QuizAttempt, Question, TopicMastery

# Unicode-aware "word" match (matches courses/ai_generation.py's _WORD_RE) so
# topic-relevance matching also works on Arabic/other non-Latin topic names.
_WORD_RE = re.compile(r"[^\W\d_]{3,}", re.UNICODE)

# Generic words that appear in almost every topic name ("intro", "basics",
# "overview"...) shouldn't count as a relevance signal on their own, or every
# material would look "relevant" to every topic.
_STOPWORDS = {
    "and", "the", "for", "with", "intro", "introduction", "basics", "basic",
    "overview", "fundamentals", "الى", "في", "على", "مقدمة", "أساسيات",
}


def get_relevant_materials(course, topic_name, limit=3):
    """Return the course's readable Materials that actually mention the
    given topic (by title or content), most relevant first.

    Topic and Material are not directly linked in the data model -- a
    Material only belongs to a Course (optionally a Module/Lesson). Blindly
    grabbing "the first few materials in the course" as grounding for a
    *specific* topic (as explain_topic/generate_practice_quiz/
    generate_quiz_for_topic used to) means a student asking about one topic
    can be shown an AI explanation built entirely from a different topic's
    material, with no indication of the mismatch. Scoring materials by
    keyword overlap with the topic name -- and returning nothing rather than
    an unrelated fallback when nothing matches -- keeps the AI honestly
    grounded (or honestly ungrounded) instead of silently mixing topics.
    """
    from .models import Material

    keywords = {w.lower() for w in _WORD_RE.findall(topic_name or "")} - _STOPWORDS
    candidates = list(
        Material.objects.filter(course=course, extraction_status=Material.ExtractionStatus.DONE)
        .exclude(extracted_text="")
    )
    if not keywords or not candidates:
        return []

    # Whole-word matching, not substring counting: a plain `.count(kw)` on a
    # short keyword like "orm" (from "Django ORM") would also match inside
    # totally unrelated words such as "perform" or "information", which is
    # exactly the kind of false-positive relevance match that caused
    # off-topic material to leak into an explanation in the first place.
    patterns = {kw: re.compile(rf"\b{re.escape(kw)}\b", re.UNICODE | re.IGNORECASE) for kw in keywords}

    scored = []
    for m in candidates:
        haystack = f"{m.title}\n{m.extracted_text}"
        score = sum(len(p.findall(haystack)) for p in patterns.values())
        if score > 0:
            scored.append((score, m))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [m for _, m in scored[:limit]]


def submit_attempt(student, quiz, answers):
    """answers: dict[question_id] -> {"choice_id": int} for mcq or {"text": str} for essay."""
    from .models import QuestionResponse

    attempt = QuizAttempt.objects.create(quiz=quiz, student=student)
    has_pending_essay = False

    for question in quiz.questions.all():
        ans = answers.get(str(question.id)) or answers.get(question.id) or {}
        if question.question_type == Question.Type.MCQ:
            choice_id = ans.get("choice_id")
            choice = question.choices.filter(id=choice_id).first() if choice_id else None
            QuestionResponse.objects.create(
                attempt=attempt, question=question, selected_choice=choice,
                is_correct=bool(choice and choice.is_correct),
            )
        else:  # essay
            has_pending_essay = True
            QuestionResponse.objects.create(
                attempt=attempt, question=question,
                answer_text=(ans.get("text") or "").strip(),
                needs_review=True,
            )

    _recompute_score(attempt)
    attempt.completed_at = timezone.now()
    attempt.status = QuizAttempt.Status.PENDING_REVIEW if has_pending_essay else QuizAttempt.Status.GRADED
    attempt.save(update_fields=["completed_at", "status"])

    if not has_pending_essay and quiz.topic:
        _update_mastery(student, quiz.topic, attempt.score or 0)

    return attempt


def grade_essay_response(response, score_0_to_1, feedback, grader):
    response.manual_score = max(0.0, min(1.0, score_0_to_1))
    response.is_correct = response.manual_score >= 0.5
    response.needs_review = False
    response.feedback = feedback
    response.graded_by = grader
    response.graded_at = timezone.now()
    response.save(update_fields=["manual_score", "is_correct", "needs_review", "feedback", "graded_by", "graded_at"])

    attempt = response.attempt
    _recompute_score(attempt)
    still_pending = attempt.responses.filter(needs_review=True).exists()
    attempt.status = QuizAttempt.Status.PENDING_REVIEW if still_pending else QuizAttempt.Status.GRADED
    attempt.save(update_fields=["status"])

    if not still_pending and attempt.quiz.topic:
        _update_mastery(attempt.student, attempt.quiz.topic, attempt.score or 0)

    return attempt


def _recompute_score(attempt):
    responses = list(attempt.responses.all())
    total = len(responses)
    if not total:
        attempt.score = None
        attempt.save(update_fields=["score"])
        return

    points = 0.0
    for r in responses:
        if r.question.question_type == Question.Type.MCQ:
            points += 1.0 if r.is_correct else 0.0
        else:
            points += r.manual_score if r.manual_score is not None else 0.0
    attempt.score = round(points / total * 100, 1)
    attempt.save(update_fields=["score"])


def _update_mastery(student, topic, latest_score):
    record, _ = TopicMastery.objects.get_or_create(student=student, topic=topic)
    n = record.attempts_count
    record.mastery_percent = ((record.mastery_percent * n) + latest_score) / (n + 1)
    record.attempts_count = n + 1
    record.save(update_fields=["mastery_percent", "attempts_count", "updated_at"])


def topic_priority(mastery_percent):
    """HIGH/MEDIUM/LOW study priority from a mastery percent. Used by
    create_study_plan (agents/tools.py) to turn "these are your weak
    topics" into an adaptive, ranked plan instead of a flat topic list."""
    if mastery_percent is None or mastery_percent < 50:
        return "high"
    if mastery_percent < 75:
        return "medium"
    return "low"


def compute_recommendations(student, limit=5):
    """Rule-based "Recommended for you" list: weakest-mastered topics first,
    then topics with repeated recent wrong answers, then topics the student
    has never been tested on. Each item names a concrete, human reason (a
    score, a wrong-answer count, or "never practiced") rather than a bare
    topic name, matching what a student actually needs to decide what to do
    next. Deliberately plain Python/ORM, not an LLM call -- there is nothing
    here an LLM would improve on, and it needs to be fast enough to render
    on every dashboard load."""
    from .models import Course, Topic, QuestionResponse

    courses = Course.objects.filter(students=student)
    topics = {t.id: t for t in Topic.objects.filter(course__in=courses)}
    if not topics:
        return []
    mastery_by_topic = {m.topic_id: m for m in TopicMastery.objects.filter(student=student, topic_id__in=topics)}

    recs = []
    covered = set()

    weak = sorted(
        (m for m in mastery_by_topic.values() if m.mastery_percent < 60),
        key=lambda m: m.mastery_percent,
    )
    for m in weak:
        recs.append({"title": f"Review {m.topic.name}", "reason": f"Quiz score {m.mastery_percent:.0f}%"})
        covered.add(m.topic_id)

    recent_responses = (
        QuestionResponse.objects.filter(
            attempt__student=student, attempt__quiz__topic_id__in=topics.keys(),
            is_correct=False, needs_review=False,
        ).select_related("question__topic").order_by("-attempt__completed_at")[:30]
    )
    wrong_counts = {}
    for r in recent_responses:
        topic = r.question.topic
        if topic and topic.id not in covered:
            wrong_counts[topic] = wrong_counts.get(topic, 0) + 1
    for topic, count in sorted(wrong_counts.items(), key=lambda kv: -kv[1]):
        if count >= 2:
            recs.append({"title": f"Practice {topic.name}", "reason": f"{count} incorrect answers recently"})
            covered.add(topic.id)

    for tid, topic in topics.items():
        if tid not in mastery_by_topic and tid not in covered:
            recs.append({"title": f"Take {topic.name} Quiz", "reason": "You haven't practiced this topic recently"})
            covered.add(tid)

    return recs[:limit]


def compute_learning_dashboard(student):
    """Aggregate stats behind the student's Learning Analytics page
    (agents/tools.py:get_learning_dashboard) -- overall progress, quiz
    average, strongest/weakest topic, materials completed, and study
    streak. All plain aggregation; the AI-written one-paragraph narrative
    on top is generated separately from these same numbers (see
    ai_generation.narrate_student_progress) so it can never contradict them."""
    from .models import Course, Material, MaterialProgress

    courses = Course.objects.filter(students=student)
    mastery = list(TopicMastery.objects.filter(student=student, topic__course__in=courses).select_related("topic"))
    attempts = QuizAttempt.objects.filter(student=student, quiz__course__in=courses, completed_at__isnull=False)

    overall = round(sum(m.mastery_percent for m in mastery) / len(mastery), 1) if mastery else None
    quiz_avg = attempts.aggregate(avg=Avg("score"))["avg"]
    quiz_avg = round(quiz_avg, 1) if quiz_avg is not None else None

    strongest = max(mastery, key=lambda m: m.mastery_percent) if mastery else None
    weakest = min(mastery, key=lambda m: m.mastery_percent) if mastery else None

    total_materials = Material.objects.filter(course__in=courses).count()
    completed_materials = MaterialProgress.objects.filter(
        student=student, material__course__in=courses, is_done=True
    ).count()

    return {
        "overall_progress_percent": overall,
        "quiz_average": quiz_avg,
        "strongest_topic": strongest.topic.name if strongest else None,
        "weakest_topic": weakest.topic.name if weakest and (not strongest or weakest.topic_id != strongest.topic_id) else None,
        "completed_materials": completed_materials,
        "total_materials": total_materials,
        "study_streak_days": _study_streak_days(student, courses),
    }


def _study_streak_days(student, courses):
    """Consecutive days up to and including today with at least one
    completed quiz attempt or a material marked studied."""
    from .models import MaterialProgress

    dates = set()
    for dt in QuizAttempt.objects.filter(
        student=student, quiz__course__in=courses, completed_at__isnull=False
    ).values_list("completed_at", flat=True):
        dates.add(timezone.localtime(dt).date())
    for dt in MaterialProgress.objects.filter(
        student=student, material__course__in=courses, is_done=True, done_at__isnull=False
    ).values_list("done_at", flat=True):
        dates.add(timezone.localtime(dt).date())

    if not dates:
        return 0
    streak = 0
    cursor = timezone.localdate()
    while cursor in dates:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def compute_class_performance(course, at_risk_threshold=50):
    """Instructor-facing counterpart to compute_learning_dashboard: hardest
    topic, class average score, and how many students are at risk (mirrors
    the "no data yet" = at-risk convention already used by
    agents/tools.py:get_at_risk_students, so the two numbers agree)."""
    avg_score = QuizAttempt.objects.filter(
        quiz__course=course, completed_at__isnull=False
    ).aggregate(avg=Avg("score"))["avg"]

    hardest = (
        TopicMastery.objects.filter(topic__course=course)
        .values("topic__id", "topic__name")
        .annotate(avg=Avg("mastery_percent"))
        .order_by("avg")
        .first()
    )

    at_risk_count = 0
    for student in course.students.all():
        records = list(TopicMastery.objects.filter(student=student, topic__course=course))
        avg = (sum(r.mastery_percent for r in records) / len(records)) if records else 0
        if avg < at_risk_threshold:
            at_risk_count += 1

    return {
        "course": course.title,
        "average_score": round(avg_score, 1) if avg_score is not None else None,
        "most_difficult_topic": hardest["topic__name"] if hardest else None,
        "most_difficult_topic_id": hardest["topic__id"] if hardest else None,
        "most_difficult_topic_avg_mastery": round(hardest["avg"], 1) if hardest else None,
        "at_risk_count": at_risk_count,
        "student_count": course.students.count(),
    }


# Kept for the seed script (simulates historical mcq-only attempts quickly).
def grade_attempt(attempt):
    _recompute_score(attempt)
    attempt.completed_at = timezone.now()
    attempt.status = QuizAttempt.Status.GRADED
    attempt.save(update_fields=["completed_at", "status"])
    if attempt.quiz.topic:
        _update_mastery(attempt.student, attempt.quiz.topic, attempt.score or 0)
    return attempt.score
