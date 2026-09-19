"""Turns a raw agent-tool result dict into a small, template-friendly
structure so the dashboards can render it as clean formatted content --
headings, paragraphs, lists, tables -- instead of dumping a Python dict
repr or a single pipe-joined sentence into the Django messages banner
(see agents/views.py:_flash and templates/partials/agent_result.html).

This never touches *what* a tool returns -- only how the same data gets
displayed after a successful dashboard action.
"""

# Short, human toast labels for the small "it worked" notification. Anything
# not listed falls back to a generic "<Action name> completed" label.
ACTION_LABELS = {
    "explain_topic": "Explanation ready",
    "generate_practice_quiz": "Practice quiz ready",
    "summarize_material": "Summary ready",
    "generate_summary_from_material": "Material summary ready",
    "generate_course_summary": "Course summary ready",
    "generate_quiz_from_material": "Quiz generated",
    "generate_quiz_for_topic": "Quiz generated",
    "create_study_plan": "Study plan created",
    "create_practice_task": "Practice task added",
    "mark_task_done": "Task updated",
    "delete_practice_task": "Task deleted",
    "recommend_quiz": "Quiz recommendation ready",
    "add_question_to_quiz": "Question added",
    "create_quiz": "Quiz created",
    "cancel_quiz": "Quiz cancelled",
    "grade_essay_response": "Response graded",
    "review_study_plan": "Study plan reviewed",
    "get_at_risk_students": "At-risk report ready",
    "send_announcement": "Announcement sent",
    "upload_material": "Material uploaded",
    "delete_material": "Material deleted",
    "get_performance": "Performance loaded",
    "get_course_analytics": "Analytics loaded",
    "get_student_performance": "Student report loaded",
    "ask_about_material": "AI Tutor replied",
    "mark_material_done": "Progress updated",
    "get_learning_dashboard": "Analytics loaded",
    "get_recommendations": "Recommendations ready",
    "start_adaptive_practice": "Practice started",
    "submit_adaptive_answer": "Answer graded",
    "end_adaptive_practice": "Practice ended",
    "analyze_class_performance": "Class insight ready",
    "generate_remedial_quiz": "Remedial quiz generated",
}

# Bare foreign-key ids are internal bookkeeping, not something a non-technical
# user needs to see in a result card -- the record they point to is already
# shown elsewhere in the UI (the quiz list, the study plan tab, etc.).
_ID_SUFFIX = "_id"
_ALWAYS_HIDDEN = {"ok"}


def toast_label(action):
    """One short line for the success notification -- never the payload."""
    return ACTION_LABELS.get(action, f"{action.replace('_', ' ').capitalize()} completed")


def build_display(action, data):
    """Return a dict the agent_result partial knows how to render:
      {"kind": "text", "title": ..., "text": ...}
      {"kind": "quiz", "title": ..., "questions": [...]}
      {"kind": "kv",   "title": ..., "rows": [...]}   (generic fallback)
    """
    if not isinstance(data, dict):
        return {"kind": "text", "title": toast_label(action), "text": str(data)}

    if data.get("explanation"):
        return {"kind": "text", "title": f"🎓 {data.get('topic') or 'Topic explanation'}", "text": data["explanation"]}

    if data.get("practice_questions") is not None:
        title = "📝 Practice quiz"
        if data.get("topic"):
            title += f" — {data['topic']}"
        return {"kind": "quiz", "title": title, "questions": data["practice_questions"]}

    if data.get("tutor_reply"):
        mode_titles = {
            "ask": "🎓 AI Tutor", "explain_simply": "🎓 Explained simply", "give_example": "🎓 Example",
            "explain_paragraph": "🎓 Paragraph explained", "hint": "💡 Hint (not the answer)",
        }
        title = mode_titles.get(data.get("mode"), "🎓 AI Tutor")
        if data.get("material"):
            title += f" — {data['material']}"
        return {"kind": "text", "title": title, "text": data["tutor_reply"]}

    if data.get("feedback") and ("next_question" in data or data.get("ended")):
        icon = "✅" if data.get("was_correct") else "❌"
        title = f"{icon} {data.get('topic', 'Practice')}"
        if data.get("score"):
            title += f" — Score {data['score']}"
        text = data["feedback"]
        if not data.get("ended"):
            text += "\n\nYour next question is ready below."
        else:
            text += "\n\nNo more questions could be generated -- session ended."
        return {"kind": "text", "title": title, "text": text}

    if data.get("question") and data.get("choices") and "feedback" not in data:
        title = f"🚀 Adaptive practice started — {data.get('topic', '')}"
        if data.get("difficulty"):
            title += f" ({data['difficulty']})"
        return {"kind": "text", "title": title, "text": "Your first question is ready below."}

    if "questions_asked" in data and "final_difficulty" in data:
        return {
            "kind": "text",
            "title": f"🏁 Practice ended — {data.get('topic', '')}",
            "text": f"You answered {data.get('questions_correct', 0)}/{data.get('questions_asked', 0)} correctly. "
                    f"Final difficulty: {data.get('final_difficulty', '')}.",
        }

    if data.get("insight") and data.get("course") and "most_difficult_topic" in data:
        return {"kind": "text", "title": f"🧠 Class performance — {data['course']}", "text": data["insight"]}

    if data.get("summary") and data.get("material"):
        title = f"✨ Summary — {data['material']}"
        if data.get("style"):
            title += f" ({data['style'].replace('_', ' ')})"
        return {"kind": "text", "title": title, "text": data["summary"]}

    if data.get("summary") and data.get("course") and "plan_id" not in data:
        return {"kind": "text", "title": f"📊 {data['course']} — AI summary", "text": data["summary"]}

    if data.get("summary") and "plan_id" in data:
        return {"kind": "text", "title": "🗺️ Study plan", "text": data["summary"]}

    return {"kind": "kv", "title": toast_label(action), "rows": _build_rows(data)}


def _humanize_key(key):
    return key.replace("_", " ").strip().capitalize()


def _stringify(value):
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, list):
        return ", ".join(_stringify(v) for v in value)
    return str(value)


def _render_value(value):
    if value is None or value == "":
        return {"kind": "plain", "text": "—"}
    if isinstance(value, bool):
        return {"kind": "plain", "text": "Yes" if value else "No"}
    if isinstance(value, (int, float)):
        return {"kind": "plain", "text": str(value)}
    if isinstance(value, str):
        if len(value) > 80 or "\n" in value:
            return {"kind": "text", "text": value}
        return {"kind": "plain", "text": value}
    if isinstance(value, list):
        if not value:
            return {"kind": "plain", "text": "—"}
        if all(isinstance(item, dict) for item in value):
            headers = [h for h in value[0].keys() if not h.endswith(_ID_SUFFIX)]
            table_rows = [[_stringify(item.get(h)) for h in headers] for item in value]
            return {"kind": "table", "headers": [_humanize_key(h) for h in headers], "rows": table_rows}
        return {"kind": "list", "items": [_stringify(item) for item in value]}
    if isinstance(value, dict):
        return {"kind": "kv", "rows": _build_rows(value)}
    return {"kind": "plain", "text": str(value)}


def _build_rows(data):
    rows = []
    for key, value in data.items():
        if key in _ALWAYS_HIDDEN or key.endswith(_ID_SUFFIX):
            continue
        rows.append({"label": _humanize_key(key), **_render_value(value)})
    return rows
