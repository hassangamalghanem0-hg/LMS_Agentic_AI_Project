"""Student Agent: the only entry point a student's natural-language message
or UI action should go through when it needs to *do* something (as opposed
to the read-only Chatbot). Exposes exactly the tools the RBAC matrix grants
students: get_performance, create_study_plan, create_practice_task,
recommend_quiz, delete_practice_task (destructive).
"""
from . import tools, nlu
from .llm_orchestrator import llm_available, run_tool_loop, LLMUnavailableError

AGENT_NAME = "student_agent"

TOOL_FUNCS = {
    "get_performance": tools.get_performance,
    "create_study_plan": tools.create_study_plan,
    "create_practice_task": tools.create_practice_task,
    "recommend_quiz": tools.recommend_quiz,
    "delete_practice_task": tools.delete_practice_task,
    "mark_task_done": tools.mark_task_done,
    "explain_topic": tools.explain_topic,
    "generate_practice_quiz": tools.generate_practice_quiz,
    "summarize_material": tools.summarize_material,
    "ask_about_material": tools.ask_about_material,
    "mark_material_done": tools.mark_material_done,
    "get_learning_dashboard": tools.get_learning_dashboard,
    "get_recommendations": tools.get_recommendations,
    "start_adaptive_practice": tools.start_adaptive_practice,
    "submit_adaptive_answer": tools.submit_adaptive_answer,
    "end_adaptive_practice": tools.end_adaptive_practice,
}

TOOL_SPECS = [
    {
        "name": "get_performance",
        "description": "Get the current student's mastery percentage per topic across all enrolled courses.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "create_study_plan",
        "description": "Create a pending study plan for the student in a course, based on their weakest topics.",
        "input_schema": {
            "type": "object",
            "properties": {"course_id": {"type": "integer"}},
            "required": ["course_id"],
        },
    },
    {
        "name": "create_practice_task",
        "description": "Add a practice task to an existing study plan for a given topic.",
        "input_schema": {
            "type": "object",
            "properties": {
                "plan_id": {"type": "integer"},
                "topic_id": {"type": "integer"},
                "description": {"type": "string"},
            },
            "required": ["plan_id", "topic_id"],
        },
    },
    {
        "name": "recommend_quiz",
        "description": "Recommend an active quiz for a given topic.",
        "input_schema": {
            "type": "object",
            "properties": {"topic_id": {"type": "integer"}},
            "required": ["topic_id"],
        },
    },
    {
        "name": "delete_practice_task",
        "description": "Delete a practice task. Destructive: first call returns a confirmation "
                       "prompt; call again with confirm=true to actually delete.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer"},
                "confirm": {"type": "boolean"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "mark_task_done",
        "description": "Mark a practice task done or not-done. Not destructive, reversible.",
        "input_schema": {
            "type": "object",
            "properties": {"task_id": {"type": "integer"}, "is_done": {"type": "boolean"}},
            "required": ["task_id"],
        },
    },
    {
        "name": "explain_topic",
        "description": "Ask the AI tutor to explain a course topic in plain language, grounded in the "
                       "course's uploaded materials when available.",
        "input_schema": {
            "type": "object",
            "properties": {"topic_id": {"type": "integer"}},
            "required": ["topic_id"],
        },
    },
    {
        "name": "generate_practice_quiz",
        "description": "AI-generate a short self-practice quiz (answers revealed immediately, not graded) "
                       "for a practice task's topic, so the student can drill exactly what they're "
                       "meant to be strengthening. Pass task_id if practicing a specific practice task, "
                       "or topic_id directly.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer"},
                "topic_id": {"type": "integer"},
                "num_mcq": {"type": "integer"},
            },
        },
    },
    {
        "name": "summarize_material",
        "description": "Summarize an uploaded course material in the student's preferred format: "
                       "'bullet_points' (default, quick key points), 'detailed' (fuller paragraphs), "
                       "'tldr' (one or two sentences), 'exam_prep' (Q&A pairs to drill), or "
                       "'eli5' (explained very simply). This is the student's own personalized "
                       "summary -- separate from any summary the instructor generated.",
        "input_schema": {
            "type": "object",
            "properties": {
                "material_id": {"type": "integer"},
                "style": {
                    "type": "string",
                    "enum": ["bullet_points", "detailed", "tldr", "exam_prep", "eli5"],
                },
            },
            "required": ["material_id"],
        },
    },
    {
        "name": "ask_about_material",
        "description": "AI Tutor for ONE specific lecture/material. mode='ask' answers a free "
                       "question (pass it in `question`); 'explain_simply' explains the lecture in "
                       "the simplest terms; 'give_example' gives a concrete example; "
                       "'explain_paragraph' explains a specific passage (paste it in `question`); "
                       "'hint' gives a nudge without the answer for what the student is stuck on "
                       "(describe it in `question`); 'quiz_me' generates a short quiz on just this "
                       "lecture.",
        "input_schema": {
            "type": "object",
            "properties": {
                "material_id": {"type": "integer"},
                "mode": {
                    "type": "string",
                    "enum": ["ask", "explain_simply", "give_example", "explain_paragraph", "hint", "quiz_me"],
                },
                "question": {"type": "string"},
            },
            "required": ["material_id"],
        },
    },
    {
        "name": "mark_material_done",
        "description": "Mark a course material as studied (or not). Not destructive, reversible.",
        "input_schema": {
            "type": "object",
            "properties": {"material_id": {"type": "integer"}, "is_done": {"type": "boolean"}},
            "required": ["material_id"],
        },
    },
    {
        "name": "get_learning_dashboard",
        "description": "Get the student's Learning Analytics: overall progress, quiz average, "
                       "strongest/weakest topic, materials completed, study streak, and an AI-written "
                       "progress note.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_recommendations",
        "description": "Get a short, ranked 'Recommended for you' list of what to study next, each "
                       "with a concrete reason.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "start_adaptive_practice",
        "description": "Start an adaptive practice session on a topic: one MCQ at a time, with "
                       "difficulty that rises after streaks of correct answers and eases after a "
                       "wrong one. Ends any previous active session first.",
        "input_schema": {
            "type": "object",
            "properties": {"topic_id": {"type": "integer"}},
            "required": ["topic_id"],
        },
    },
    {
        "name": "submit_adaptive_answer",
        "description": "Answer the current question of the active adaptive practice session by its "
                       "choice index (0-based), and get the next question.",
        "input_schema": {
            "type": "object",
            "properties": {"selected_index": {"type": "integer"}},
            "required": ["selected_index"],
        },
    },
    {
        "name": "end_adaptive_practice",
        "description": "End the active adaptive practice session and get a summary.",
        "input_schema": {"type": "object", "properties": {}},
    },
]

SYSTEM_PROMPT = (
    "You are the Student Agent of an LMS. You act ONLY on behalf of the currently "
    "authenticated student and ONLY through the tools you were given. Never invent "
    "data; always call a tool to read or change anything. Destructive tools require "
    "explicit confirmation -- if a tool tells you it needs confirmation, relay that "
    "to the student and wait for them to confirm before calling it again with "
    "confirm=true. When the student asks for a lecture/material summary, ask (or infer "
    "from how they phrased it) which style they want -- bullet_points, detailed, tldr, "
    "exam_prep, or eli5 -- before calling summarize_material; default to bullet_points "
    "if they don't care. When the student asks something about a specific lecture they're "
    "reading, use ask_about_material rather than explain_topic. For 'quiz me on X topic' "
    "without a specific lecture open, use generate_practice_quiz; for adaptive, "
    "progressively-harder practice, use start_adaptive_practice / submit_adaptive_answer. "
    "Keep replies short and concrete."
)


def call_tool(user, name, **params):
    func = TOOL_FUNCS.get(name)
    if not func:
        return {"ok": False, "error": f"Unknown tool '{name}'."}
    return func(user, **params)


def handle_message(user, message, history=None):
    """Freeform chat entry point. Uses Gemini tool-calling when a working key
    is configured; otherwise -- or when the model is rate-limited/unreachable
    -- falls back to the offline rule-based router in agents/nlu.py, which
    understands every tool this agent exposes. Either way the student gets a
    real action, never a dead end."""
    bound_impls = {name: (lambda _n=name, **p: call_tool(user, _n, **p)) for name in TOOL_FUNCS}

    if llm_available():
        try:
            return run_tool_loop(SYSTEM_PROMPT, TOOL_SPECS, bound_impls, message, history)
        except LLMUnavailableError as e:
            result = _fallback_router(user, message, bound_impls)
            # Only mention the degraded mode when we couldn't actually do the
            # thing -- if the offline router ran the right tool, the student
            # doesn't need an apology for a request that succeeded.
            if not result.get("tool_calls"):
                result["reply"] = f"\u26a0 {e}\n\n" + result["reply"]
            return result

    return _fallback_router(user, message, bound_impls)


def _fallback_router(user, message, bound_impls):
    """Offline mode: real intent routing, not a canned help string."""
    routed = nlu.route_student(user, message, bound_impls)
    if routed is not None:
        return routed
    return {"reply": nlu.STUDENT_HELP, "tool_calls": []}
