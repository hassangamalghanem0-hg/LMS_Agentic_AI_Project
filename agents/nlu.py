"""Offline natural-language router -- the agents' "built-in engine".

Both agents used to have a stub fallback: the student one only understood the
word "performance", the instructor one understood nothing at all. So whenever
Gemini was rate-limited, every message came back as the same canned "use the
dashboard actions" paragraph -- which reads, correctly, as a broken agent.

This module replaces both stubs with a real rule-based NLU layer that:

  * classifies the message into one of the agent's actual tools (English and
    Egyptian/Modern-Standard Arabic phrasings for each),
  * resolves the entities the tool needs -- course, topic, material, student,
    quiz, study plan, practice task -- by fuzzy-matching their names against
    the message, falling back to "the only one you have" when the user has
    exactly one candidate, and to the weakest/most relevant one where that is
    the obvious default,
  * calls the real tool (so RBAC, the audit log and the confirmation flow all
    still apply -- this layer never touches the database itself), and
  * renders the tool's result as a readable reply.

The result: with no API key at all, or with the key fully rate-limited, every
agent capability still works from plain typed sentences. The LLM path stays
the preferred one (it handles phrasings no rule set can), but it is now an
upgrade rather than a dependency.
"""
import re
import unicodedata

from courses.models import Course, Topic, Material, Quiz
from planning.models import StudyPlan, PracticeTask, AdaptivePracticeSession

# --------------------------------------------------------------------------
# Text normalisation
# --------------------------------------------------------------------------

_ARABIC_DIACRITICS = re.compile(r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED\u0640]")


def normalize(text):
    """Lowercase, strip Arabic diacritics/tatweel, and unify the Arabic letter
    forms people type interchangeably (أ/إ/آ -> ا, ى -> ي, ة -> ه) so that
    'الملخّص' and 'الملخص' match the same keyword."""
    text = unicodedata.normalize("NFKC", text or "")
    text = _ARABIC_DIACRITICS.sub("", text)
    text = (text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
                .replace("ى", "ي").replace("ة", "ه").replace("ؤ", "و").replace("ئ", "ي"))
    return text.lower().strip()


def _tokens(text):
    return [t for t in re.split(r"[^\w\u0600-\u06FF]+", normalize(text)) if t]


def has_any(text, keywords):
    """True if any keyword appears in the text. Both sides go through
    normalize(), so a keyword written naturally in Arabic ("إحصائيات") still
    matches however the user typed it -- without that, the keyword and the
    normalised message end up in two different spellings and never meet."""
    text = normalize(text)
    return any(normalize(k) in text for k in keywords)


# --------------------------------------------------------------------------
# Entity resolution
# --------------------------------------------------------------------------

# Words that carry no identifying information, so they shouldn't count towards
# a name match ("the Django ORM lecture" should match a topic called
# "Django ORM" on django+orm, not on "lecture").
_STOPWORDS = {
    "the", "a", "an", "of", "for", "on", "in", "to", "my", "me", "please", "can", "you",
    "course", "topic", "lecture", "material", "quiz", "exam", "test", "file", "about",
    "give", "show", "make", "create", "generate", "want", "need", "and", "with",
    "في", "من", "على", "عن", "ال", "ده", "دي", "لو", "سمحت", "عايز", "عاوز", "ممكن",
    "اعمل", "اعملي", "ليا", "بتاع", "بتاعت", "كورس", "ماده", "محاضره", "موضوع",
}


def _explicit_id(text, *labels):
    """Pull an explicit id out of phrasings like 'course 3', 'task #12',
    'material id 7', 'الكورس رقم 2'."""
    for label in labels:
        m = re.search(rf"{label}\s*(?:id\s*)?#?\s*(\d+)", text)
        if m:
            return int(m.group(1))
    m = re.search(r"(?:رقم|#)\s*(\d+)", text)
    return int(m.group(1)) if m else None


def _name_score(name, message_norm, message_tokens):
    """How strongly `name` is referenced in the message. 1.0 = the whole name
    appears verbatim; otherwise the fraction of the name's meaningful words
    that appear as message tokens."""
    name_norm = normalize(name)
    if not name_norm:
        return 0.0
    if name_norm in message_norm:
        return 1.0
    parts = [t for t in _tokens(name_norm) if t not in _STOPWORDS and len(t) > 2]
    if not parts:
        return 0.0

    def hit(part):
        if part in message_tokens:
            return True
        # Prefix matching only, and only between two reasonably long words --
        # plain substring matching made every short message token ("a", "on")
        # match inside every long topic name, so unrelated topics tied at a
        # perfect score and the router gave up as "ambiguous".
        return any(
            len(tok) >= 4 and len(part) >= 4 and (tok.startswith(part) or part.startswith(tok))
            for tok in message_tokens
        )

    return sum(1 for p in parts if hit(p)) / len(parts)


def match_one(queryset, text, name_attr="title", id_labels=(), threshold=0.5, allow_single=True):
    """Resolve one object a message refers to.

    Order of preference: an explicit id, then the best fuzzy name match above
    the threshold, then -- if the user only has one candidate at all -- that
    one. Returns None when genuinely ambiguous, so the caller can ask.
    """
    items = list(queryset)
    if not items:
        return None

    obj_id = _explicit_id(text, *id_labels) if id_labels else None
    if obj_id is not None:
        for item in items:
            if item.id == obj_id:
                return item

    message_tokens = set(_tokens(text))
    scored = sorted(
        ((_name_score(getattr(item, name_attr, ""), text, message_tokens), item) for item in items),
        key=lambda pair: pair[0],
        reverse=True,
    )
    if scored and scored[0][0] >= threshold:
        # Reject a tie between two different objects -- better to ask.
        if len(scored) > 1 and scored[1][0] == scored[0][0] and scored[1][1].id != scored[0][1].id:
            return None
        return scored[0][1]

    if allow_single and len(items) == 1:
        return items[0]
    return None


def _listing(items, name_attr="title", limit=8):
    names = [f"#{i.id} {getattr(i, name_attr)}" for i in items[:limit]]
    extra = f" (+{len(items) - limit} more)" if len(items) > limit else ""
    return ", ".join(names) + extra if names else "none"


def wants_confirmation(text):
    return has_any(text, ["confirm", "yes", "sure", "go ahead", "اكد", "تاكيد", "ايوه", "ايوا", "نعم", "اه", "فعلا"])


# --------------------------------------------------------------------------
# Result rendering
# --------------------------------------------------------------------------

def _pct(value):
    return "n/a" if value is None else f"{float(value):.0f}%"


def _render(name, out):
    """Turn a tool result into a sentence or short block the chat can show."""
    if not isinstance(out, dict):
        return str(out)
    if out.get("requires_confirmation"):
        return out.get("message", "That action needs confirmation -- say 'confirm' to go ahead.")
    if not out.get("ok"):
        return f"I couldn't do that: {out.get('error', 'unknown error')}"

    data = out.get("data")
    if not isinstance(data, dict):
        return str(data)

    if name == "get_performance":
        topics = data.get("topics") or []
        if not topics:
            return "You don't have any quiz history yet, so there's no mastery data."
        lines = "\n".join(
            f"• {t['topic']} — {_pct(t.get('mastery_percent'))} ({t.get('attempts', 0)} attempt(s))"
            for t in topics
        )
        return "Your current mastery:\n" + lines

    if name == "create_study_plan":
        return (f"Created study plan #{data.get('plan_id')} "
                f"(status: {data.get('status', 'pending')}, "
                f"{data.get('seeded_task_count', 0)} task(s) added).\n\n"
                f"{data.get('summary', '')}").strip()

    if name == "get_learning_dashboard":
        return (
            f"Overall progress: {_pct(data.get('overall_progress_percent'))} · "
            f"Quiz average: {_pct(data.get('quiz_average'))}\n"
            f"Strongest: {data.get('strongest_topic') or 'not enough data'} · "
            f"Weakest: {data.get('weakest_topic') or 'not enough data'}\n"
            f"Materials done: {data.get('completed_materials', 0)}/{data.get('total_materials', 0)} · "
            f"Streak: {data.get('study_streak_days', 0)} day(s)\n\n"
            f"{data.get('insight', '')}"
        ).strip()

    if name == "get_recommendations":
        recs = data.get("recommendations") or []
        if not recs:
            return "Nothing to flag right now — you're on track."
        return "Recommended for you:\n" + "\n".join(
            f"• {r.get('title') or r.get('topic')} — {r.get('reason', '')}" for r in recs
        )

    if name in ("explain_topic",):
        return f"**{data.get('topic', '')}**\n\n{data.get('explanation', '')}"

    if name in ("summarize_material", "generate_summary_from_material"):
        return data.get("summary", "")

    if name == "ask_about_material":
        return data.get("answer") or data.get("response") or str(data)

    if name == "recommend_quiz":
        if not data.get("quiz_id"):
            return data.get("message", "No active quiz for that topic yet.")
        return f"Try quiz #{data['quiz_id']}: “{data.get('title')}” on {data.get('topic')}."

    if name in ("generate_practice_quiz",):
        questions = data.get("practice_questions") or data.get("questions") or []
        if not questions:
            return "I couldn't generate practice questions for that topic yet."
        blocks = []
        for i, q in enumerate(questions, 1):
            choices = q.get("choices") or []
            opts = "\n".join(f"   {chr(96 + j)}) {c.get('text', c) if isinstance(c, dict) else c}"
                             for j, c in enumerate(choices, 1))
            answer = q.get("correct_answer") or q.get("answer") or next(
                (c.get("text") for c in choices if isinstance(c, dict) and c.get("is_correct")), None)
            text = q.get("question") or q.get("text", "")
            blocks.append(f"{i}. {text}\n{opts}\n   ✅ {answer or 'see explanation'}")
        return f"Practice on {data.get('topic', 'this topic')}:\n\n" + "\n\n".join(blocks)

    if name in ("create_quiz", "generate_quiz_from_material", "generate_quiz_for_topic", "generate_remedial_quiz"):
        bits = [f"Quiz #{data.get('quiz_id')} created: “{data.get('title')}”"]
        if "mcq_count" in data:
            bits.append(f"{data.get('mcq_count', 0)} MCQ + {data.get('essay_count', 0)} essay question(s)")
        return " — ".join(bits) + "."

    if name == "get_course_analytics":
        topics = data.get("topic_breakdown") or []
        head = (f"{data.get('course', 'Course')}: average score {_pct(data.get('avg_score'))}, "
                f"{data.get('student_count', 0)} student(s), {data.get('quiz_count', 0)} quiz(zes), "
                f"{data.get('attempt_count', 0)} completed attempt(s).")
        if data.get("pending_essay_count"):
            head += f" {data['pending_essay_count']} essay answer(s) awaiting your grading."
        if not topics:
            return head
        return head + "\n" + "\n".join(
            f"• {t.get('topic')} — avg mastery {_pct(t.get('avg_mastery'))}" for t in topics)

    if name == "analyze_class_performance":
        return (
            f"Hardest topic: {data.get('most_difficult_topic') or 'not enough data'} "
            f"({_pct(data.get('most_difficult_topic_avg_mastery'))} avg mastery)\n"
            f"Class average: {_pct(data.get('average_score'))} · "
            f"At risk: {data.get('at_risk_count', 0)}/{data.get('student_count', 0)}\n\n"
            f"{data.get('insight', '')}"
        ).strip()

    if name == "get_student_performance":
        topics = data.get("topics") or []
        attempts = data.get("attempt_history") or []
        head = f"{data.get('student', 'Student')} (#{data.get('student_id')}):"
        if not topics and not attempts:
            return head + " no quiz history yet."
        lines = [f"• {t.get('topic')} — {_pct(t.get('mastery_percent'))}" for t in topics]
        if attempts:
            lines.append(f"Completed {len(attempts)} quiz attempt(s); latest: "
                         f"{attempts[-1].get('quiz')} at {_pct(attempts[-1].get('score'))}.")
        return head + "\n" + "\n".join(lines)

    if name == "get_at_risk_students":
        rows = data.get("at_risk_students") or []
        if not rows:
            return f"No student in {data.get('course', 'this course')} is below the {data.get('threshold', 50):.0f}% threshold."
        return f"At-risk students in {data.get('course')}:\n" + "\n".join(
            f"• {r.get('student')} (#{r.get('student_id')}) — {r.get('reason')}" for r in rows)

    if name == "send_announcement":
        return f"Announcement sent to {data.get('recipient_count', 0)} student(s) in {data.get('course')}."

    if name == "generate_course_summary":
        return data.get("summary") or str(data)

    if name == "review_study_plan":
        return f"Study plan #{data.get('plan_id')} is now {data.get('status')}."

    if name == "mark_task_done":
        return f"Task #{data.get('task_id')} marked {'done ✅' if data.get('is_done') else 'not done'}."

    if name == "create_practice_task":
        return f"Added practice task #{data.get('task_id')} on {data.get('topic', 'that topic')}."

    if name == "delete_practice_task":
        return f"Deleted practice task #{data.get('deleted_task_id')}."

    if name == "delete_material":
        note = f" {data.get('note')}" if data.get("note") else ""
        return f"Deleted “{data.get('title')}”.{note}"

    if name == "cancel_quiz":
        return f"Quiz “{data.get('title')}” is now inactive."

    if name == "mark_material_done":
        return f"Material #{data.get('material_id')} marked {'studied ✅' if data.get('is_done') else 'not studied'}."

    if name in ("start_adaptive_practice", "submit_adaptive_answer"):
        verdict = data.get("feedback", "")
        if data.get("ended"):
            return f"{verdict}\n\nSession ended — score {data.get('score', '')}.".strip()
        question = data.get("next_question") or data.get("question") or ""
        choices = data.get("next_choices") or data.get("choices") or []
        opts = "\n".join(f"  {i}) {c.get('text') if isinstance(c, dict) else c}" for i, c in enumerate(choices))
        head = f"[{data.get('difficulty', 'medium')} · Q{data.get('question_number', 1)}]"
        tail = "\n\nReply with the number of your answer (e.g. “answer 2”)." if choices else ""
        body = f"{head} {question}\n{opts}{tail}"
        return (f"{verdict}\n\n{body}" if verdict else body).strip()

    if name == "end_adaptive_practice":
        return (f"Practice on {data.get('topic', 'that topic')} ended — "
                f"{data.get('questions_correct', 0)}/{data.get('questions_asked', 0)} correct "
                f"(final difficulty: {data.get('final_difficulty', 'medium')}).")

    # Generic: readable key/value lines.
    parts = [f"{k.replace('_', ' ')}: {v}" for k, v in data.items()
             if not isinstance(v, (list, dict)) and v not in (None, "")]
    return "; ".join(parts) if parts else "Done."


def render_result(name, out):
    """Public alias for _render -- also used by the LLM path when the model
    runs a tool but returns no prose of its own."""
    return _render(name, out)


def _call(bound_impls, name, calls, **params):
    out = bound_impls[name](**params)
    calls.append({"name": name, "input": params, "output": out})
    return {"reply": _render(name, out), "tool_calls": calls}


def _say(text, calls=None):
    return {"reply": text, "tool_calls": calls or []}


# --------------------------------------------------------------------------
# Student router
# --------------------------------------------------------------------------

STUDENT_HELP = (
    "I can: show your performance or learning dashboard, tell you what to study next, "
    "create a study plan, add/complete/delete practice tasks, explain a topic, summarize "
    "a lecture, quiz you, or run adaptive practice. Just say it in plain words — "
    "e.g. “create a study plan”, “explain Django ORM”, “quiz me on REST APIs”, "
    "“summarize the Django ORM lecture”, “what should I study next?”."
)


def route_student(user, message, bound_impls):
    text = normalize(message)
    calls = []
    if not text:
        return _say(STUDENT_HELP)

    courses = list(Course.objects.filter(students=user))
    topics = list(Topic.objects.filter(course__students=user).select_related("course"))
    materials = list(Material.objects.filter(course__students=user).select_related("course"))

    def topic_arg():
        return match_one(topics, text, name_attr="name", id_labels=("topic", "موضوع"))

    def material_arg():
        return match_one(materials, text, name_attr="title",
                         id_labels=("material", "lecture", "محاضره", "ماده"))

    # -- adaptive practice ---------------------------------------------------
    session = AdaptivePracticeSession.objects.filter(student=user, is_active=True).first()

    if has_any(text, ["end practice", "stop practice", "انهي التدريب", "خلاص كفايه", "وقف التدريب"]):
        return _call(bound_impls, "end_adaptive_practice", calls)

    if session:
        m = re.search(r"(?:answer|choice|option|اجابه|اختار|رقم)\s*#?\s*(\d+)", text) or re.fullmatch(r"\s*(\d+)\s*", text)
        if m:
            return _call(bound_impls, "submit_adaptive_answer", calls, selected_index=int(m.group(1)))

    if has_any(text, ["adaptive", "practice mode", "تدريب متدرج", "تدريب تكيفي", "درني"]):
        topic = topic_arg()
        if not topic:
            return _say(f"Which topic should I drill you on? Yours: {_listing(topics, 'name')}.")
        return _call(bound_impls, "start_adaptive_practice", calls, topic_id=topic.id)

    # -- materials -----------------------------------------------------------
    if has_any(text, ["summarize", "summary", "لخص", "ملخص", "تلخيص"]):
        material = material_arg()
        if not material:
            return _say(f"Which lecture should I summarize? Yours: {_listing(materials)}.")
        style = "bullet_points"
        if has_any(text, ["tldr", "tl;dr", "باختصار", "في سطر"]):
            style = "tldr"
        elif has_any(text, ["detailed", "detail", "بالتفصيل", "مفصل"]):
            style = "detailed"
        elif has_any(text, ["exam", "revision", "مراجعه", "امتحان"]):
            style = "exam_prep"
        elif has_any(text, ["simple", "eli5", "بسيط", "ببساطه", "زي ما تشرح لطفل"]):
            style = "eli5"
        return _call(bound_impls, "summarize_material", calls, material_id=material.id, style=style)

    if has_any(text, ["mark", "خلصت", "ذاكرت"]) and has_any(text, ["lecture", "material", "محاضره", "ماده"]):
        material = material_arg()
        if material:
            return _call(bound_impls, "mark_material_done", calls, material_id=material.id, is_done=True)

    # -- practice tasks ------------------------------------------------------
    if has_any(text, ["delete task", "remove task", "احذف مهمه", "امسح مهمه", "الغي مهمه"]):
        task = match_one(PracticeTask.objects.filter(plan__student=user), text,
                         name_attr="description", id_labels=("task", "مهمه"), allow_single=True)
        if not task:
            return _say("Which task should I delete? Tell me its number, e.g. “delete task 4”.")
        return _call(bound_impls, "delete_practice_task", calls,
                     task_id=task.id, confirm=wants_confirmation(text))

    if (has_any(text, ["mark task", "task done", "complete task", "finished task", "خلصت مهمه", "تمت المهمه"])
            or (has_any(text, ["done", "complete", "خلصت"]) and has_any(text, ["task", "مهمه"]))):
        task = match_one(PracticeTask.objects.filter(plan__student=user, is_done=False), text,
                         name_attr="description", id_labels=("task", "مهمه"))
        if not task:
            return _say("Which task? Say e.g. “mark task 3 done”.")
        return _call(bound_impls, "mark_task_done", calls, task_id=task.id, is_done=True)

    if has_any(text, ["add task", "add a task", "new task", "ضيف مهمه", "اضف مهمه", "مهمه جديده"]):
        plan = StudyPlan.objects.filter(student=user).order_by("-created_at").first()
        if not plan:
            return _say("You don't have a study plan yet — say “create a study plan” first.")
        topic = topic_arg()
        if not topic:
            return _say(f"Which topic is the task for? Yours: {_listing(topics, 'name')}.")
        return _call(bound_impls, "create_practice_task", calls,
                     plan_id=plan.id, topic_id=topic.id,
                     description=f"Practice: {topic.name}")

    # -- study plan ----------------------------------------------------------
    if has_any(text, ["study plan", "studyplan", "خطه مذاكره", "خطه دراسيه", "خطة", "بلان"]):
        course = match_one(courses, text, id_labels=("course", "كورس"))
        if not course:
            return _say(f"Which course should the plan cover? You're enrolled in: {_listing(courses)}.")
        return _call(bound_impls, "create_study_plan", calls, course_id=course.id)

    # -- quizzes -------------------------------------------------------------
    if has_any(text, ["quiz me", "practice quiz", "practice questions", "test me", "اختبرني",
                      "اسئله تدريب", "امتحني", "درب"]):
        topic = topic_arg()
        if not topic:
            return _say(f"Which topic? Yours: {_listing(topics, 'name')}.")
        return _call(bound_impls, "generate_practice_quiz", calls, topic_id=topic.id)

    if has_any(text, ["recommend a quiz", "recommend quiz", "which quiz", "رشح", "كويز مناسب"]):
        topic = topic_arg()
        if not topic:
            return _say(f"Which topic? Yours: {_listing(topics, 'name')}.")
        return _call(bound_impls, "recommend_quiz", calls, topic_id=topic.id)

    # -- explanation ---------------------------------------------------------
    if has_any(text, ["explain", "what is", "teach me", "اشرح", "شرح", "يعني ايه", "فهمني"]):
        material = material_arg() if has_any(text, ["lecture", "material", "محاضره", "ماده", "file", "ملف"]) else None
        if material:
            return _call(bound_impls, "ask_about_material", calls,
                         material_id=material.id, mode="ask", question=message)
        topic = topic_arg()
        if not topic:
            return _say(f"Which topic should I explain? Yours: {_listing(topics, 'name')}.")
        return _call(bound_impls, "explain_topic", calls, topic_id=topic.id)

    # -- analytics -----------------------------------------------------------
    if has_any(text, ["what should i study", "what next", "recommend", "suggestion", "اذاكر ايه",
                      "ابدا منين", "نصيحه"]):
        return _call(bound_impls, "get_recommendations", calls)

    if has_any(text, ["dashboard", "analytics", "progress", "streak", "overall", "تقدمي", "احصائيات",
                      "لوحه", "انجازي"]):
        return _call(bound_impls, "get_learning_dashboard", calls)

    if has_any(text, ["performance", "mastery", "how am i doing", "score", "grade", "اداء", "درجت",
                      "مستواي", "اتقان", "درجاتي"]):
        return _call(bound_impls, "get_performance", calls)

    # -- plain lookups (no tool needed) --------------------------------------
    if has_any(text, ["my course", "my courses", "enrolled", "كورساتي", "كورسي", "مواد"]):
        return _say(f"You're enrolled in: {_listing(courses)}." if courses
                    else "You're not enrolled in any course yet.")

    if has_any(text, ["my topic", "topics", "مواضيع", "التوبيكس"]):
        return _say(f"Your topics: {_listing(topics, 'name')}.")

    if has_any(text, ["material", "lecture", "محاضرات", "الملفات"]):
        return _say(f"Your lectures/materials: {_listing(materials)}.")

    return None


# --------------------------------------------------------------------------
# Instructor router
# --------------------------------------------------------------------------

INSTRUCTOR_HELP = (
    "I can: pull course analytics or class insights, show one student's performance, flag "
    "at-risk students, send an announcement, generate a course summary, generate an AI quiz "
    "(from a lecture file, from a topic, or a remedial one for the hardest topic), summarize a "
    "material, approve/reject a study plan, cancel a quiz, or delete a material. Say it in plain "
    "words — e.g. “show analytics for Full-Stack Python”, “who is at risk?”, "
    "“make a quiz on Django ORM”, “announce that the exam moved to Thursday”."
)


def route_instructor(user, message, bound_impls):
    text = normalize(message)
    calls = []
    if not text:
        return _say(INSTRUCTOR_HELP)

    courses = list(Course.objects.filter(instructor=user))
    if not courses:
        return _say("You don't own any course yet, so there's nothing for me to act on.")

    topics = list(Topic.objects.filter(course__instructor=user).select_related("course"))
    materials = list(Material.objects.filter(course__instructor=user).select_related("course"))

    def course_arg():
        return match_one(courses, text, id_labels=("course", "كورس"))

    def topic_arg():
        return match_one(topics, text, name_attr="name", id_labels=("topic", "موضوع"))

    def material_arg():
        return match_one(materials, text, name_attr="title",
                         id_labels=("material", "lecture", "محاضره", "ماده", "ملف"))

    # -- destructive ---------------------------------------------------------
    if has_any(text, ["delete material", "remove material", "delete lecture", "احذف ماده",
                      "امسح محاضره", "احذف ملف"]):
        material = material_arg()
        if not material:
            return _say(f"Which material? Yours: {_listing(materials)}.")
        return _call(bound_impls, "delete_material", calls,
                     material_id=material.id, confirm=wants_confirmation(text))

    if has_any(text, ["cancel quiz", "deactivate quiz", "الغي كويز", "اوقف كويز", "الغاء الامتحان"]):
        quiz = match_one(Quiz.objects.filter(course__instructor=user, is_active=True), text,
                         id_labels=("quiz", "كويز", "امتحان"))
        if not quiz:
            return _say("Which quiz should I cancel? Give me its number, e.g. “cancel quiz 5”.")
        return _call(bound_impls, "cancel_quiz", calls,
                     quiz_id=quiz.id, confirm=wants_confirmation(text))

    # -- study plan review ---------------------------------------------------
    if has_any(text, ["study plan", "خطه مذاكره", "الخطه"]) or has_any(text, ["approve", "reject", "وافق", "ارفض"]):
        decision = "approved" if has_any(text, ["approve", "accept", "وافق", "اقبل", "موافق"]) else (
            "rejected" if has_any(text, ["reject", "decline", "ارفض", "مرفوض"]) else None)
        pending = list(StudyPlan.objects.filter(
            student__courses_enrolled__instructor=user, status=StudyPlan.Status.PENDING).distinct())
        if decision:
            plan = match_one(pending, text, id_labels=("plan", "خطه"))
            if not plan:
                return _say(f"Which study plan? Pending: {_listing(pending)}.")
            return _call(bound_impls, "review_study_plan", calls, plan_id=plan.id, decision=decision)
        if not pending:
            return _say("There are no study plans waiting for your review.")
        return _say(f"Pending study plans: {_listing(pending)}. Say “approve plan 3” or “reject plan 3”.")

    # -- announcements -------------------------------------------------------
    if has_any(text, ["announce", "announcement", "broadcast", "notify", "اعلان", "بلغ", "ابعت رساله"]):
        course = course_arg()
        if not course:
            return _say(f"Which course? Yours: {_listing(courses)}.")
        body = None
        quoted = re.search(r"[\"“'](.+?)[\"”']", message)
        if quoted:
            body = quoted.group(1)
        else:
            m = re.search(r"(?:that|saying|:|بان|ان|قولهم|بلغهم)\s+(.{4,})$", message, re.I | re.S)
            if m:
                body = m.group(1).strip()
        if not body:
            return _say("What should the announcement say? e.g. “announce that the exam moved to Thursday”.")
        return _call(bound_impls, "send_announcement", calls, course_id=course.id, message=body)

    # -- AI generation -------------------------------------------------------
    if has_any(text, ["remedial", "hardest topic quiz", "كويز علاجي", "الموضوع الاصعب"]):
        course = course_arg()
        if not course:
            return _say(f"Which course? Yours: {_listing(courses)}.")
        return _call(bound_impls, "generate_remedial_quiz", calls, course_id=course.id)

    if has_any(text, ["quiz", "exam", "questions", "كويز", "امتحان", "اسئله"]) and has_any(
            text, ["generate", "create", "make", "new", "build", "اعمل", "ولد", "انشئ", "جهز"]):
        material = material_arg() if has_any(
            text, ["material", "lecture", "file", "from the", "محاضره", "ماده", "ملف", "من ملف"]) else None
        if material:
            topic = topic_arg() or Topic.objects.filter(course=material.course).first()
            if not topic:
                return _say("That course has no topics yet — add one before generating a quiz.")
            return _call(bound_impls, "generate_quiz_from_material", calls,
                         material_id=material.id, topic_id=topic.id)
        topic = topic_arg()
        if not topic:
            return _say(f"Which topic should the quiz cover? Yours: {_listing(topics, 'name')}.")
        return _call(bound_impls, "generate_quiz_for_topic", calls,
                     course_id=topic.course_id, topic_id=topic.id)

    if has_any(text, ["summarize", "summary", "لخص", "ملخص", "تلخيص"]):
        if has_any(text, ["course", "كورس"]) and not has_any(text, ["material", "lecture", "محاضره", "ملف"]):
            course = course_arg()
            if not course:
                return _say(f"Which course? Yours: {_listing(courses)}.")
            return _call(bound_impls, "generate_course_summary", calls, course_id=course.id)
        material = material_arg()
        if material:
            return _call(bound_impls, "generate_summary_from_material", calls, material_id=material.id)
        course = course_arg()
        if course:
            return _call(bound_impls, "generate_course_summary", calls, course_id=course.id)
        return _say(f"Summarize what? Materials: {_listing(materials)} · Courses: {_listing(courses)}.")

    # -- analytics -----------------------------------------------------------
    if has_any(text, ["at risk", "at-risk", "struggling", "failing", "weak students", "متعثر",
                      "ضعاف", "طلاب ضعيفه", "محتاجين مساعده"]):
        course = course_arg()
        if not course:
            return _say(f"Which course? Yours: {_listing(courses)}.")
        return _call(bound_impls, "get_at_risk_students", calls, course_id=course.id)

    if has_any(text, ["insight", "class performance", "how is the class", "overall", "recommendation",
                      "تحليل", "مستوي الفصل", "مستوي الطلاب", "الفصل عامل ايه"]):
        course = course_arg()
        if not course:
            return _say(f"Which course? Yours: {_listing(courses)}.")
        return _call(bound_impls, "analyze_class_performance", calls, course_id=course.id)

    if has_any(text, ["student", "طالب", "الطالب"]) and has_any(
            text, ["performance", "mastery", "doing", "score", "اداء", "مستوي", "درجات"]):
        course = course_arg()
        if not course:
            return _say(f"Which course? Yours: {_listing(courses)}.")
        student = match_one(course.students.all(), text, name_attr="username",
                            id_labels=("student", "طالب"), allow_single=False)
        if not student:
            names = ", ".join(f"#{s.id} {s.username}" for s in course.students.all()[:10]) or "none"
            return _say(f"Which student? In {course.title}: {names}.")
        return _call(bound_impls, "get_student_performance", calls,
                     course_id=course.id, student_id=student.id)

    if has_any(text, ["analytics", "stats", "statistics", "average", "report", "احصائيات",
                      "تحليلات", "تقرير", "المتوسط"]):
        course = course_arg()
        if not course:
            return _say(f"Which course? Yours: {_listing(courses)}.")
        return _call(bound_impls, "get_course_analytics", calls, course_id=course.id)

    # -- plain lookups -------------------------------------------------------
    if has_any(text, ["my course", "my courses", "what course", "which course", "كورساتي", "كورسي"]):
        return _say(f"You teach: {_listing(courses)}.")

    if has_any(text, ["my students", "how many students", "طلابي", "عدد الطلاب"]):
        total = sum(c.students.count() for c in courses)
        return _say(f"You have {total} student(s) across {len(courses)} course(s).")

    if has_any(text, ["topics", "مواضيع", "التوبيكس"]):
        return _say(f"Your topics: {_listing(topics, 'name')}.")

    if has_any(text, ["material", "lecture", "files", "محاضرات", "الملفات"]):
        return _say(f"Your materials: {_listing(materials)}.")

    return None
