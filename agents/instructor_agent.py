"""Instructor Agent: exposes only the tools the RBAC matrix grants
instructors: get_course_analytics, get_student_performance, create_quiz,
generate_course_summary, cancel_quiz (destructive), review_study_plan."""
from . import tools, nlu
from .llm_orchestrator import llm_available, run_tool_loop, LLMUnavailableError

AGENT_NAME = "instructor_agent"

TOOL_FUNCS = {
    "get_course_analytics": tools.get_course_analytics,
    "get_student_performance": tools.get_student_performance,
    "create_quiz": tools.create_quiz,
    "generate_course_summary": tools.generate_course_summary,
    "cancel_quiz": tools.cancel_quiz,
    "review_study_plan": tools.review_study_plan,
    "upload_material": tools.upload_material,
    "generate_summary_from_material": tools.generate_summary_from_material,
    "generate_quiz_from_material": tools.generate_quiz_from_material,
    "generate_quiz_for_topic": tools.generate_quiz_for_topic,
    "add_question_to_quiz": tools.add_question_to_quiz,
    "grade_essay_response": tools.grade_essay_response,
    "delete_material": tools.delete_material,
    "get_at_risk_students": tools.get_at_risk_students,
    "send_announcement": tools.send_announcement,
    "analyze_class_performance": tools.analyze_class_performance,
    "generate_remedial_quiz": tools.generate_remedial_quiz,
}

TOOL_SPECS = [
    {
        "name": "get_course_analytics",
        "description": "Get aggregate analytics (avg score, topic breakdown) for a course the instructor owns.",
        "input_schema": {"type": "object", "properties": {"course_id": {"type": "integer"}}, "required": ["course_id"]},
    },
    {
        "name": "get_student_performance",
        "description": "Get one student's per-topic mastery within a course the instructor owns.",
        "input_schema": {
            "type": "object",
            "properties": {"course_id": {"type": "integer"}, "student_id": {"type": "integer"}},
            "required": ["course_id", "student_id"],
        },
    },
    {
        "name": "create_quiz",
        "description": "Create a new quiz for a topic in a course the instructor owns.",
        "input_schema": {
            "type": "object",
            "properties": {
                "course_id": {"type": "integer"},
                "topic_id": {"type": "integer"},
                "title": {"type": "string"},
            },
            "required": ["course_id", "topic_id"],
        },
    },
    {
        "name": "generate_course_summary",
        "description": "Generate a natural-language summary of a course's size and weakest topics.",
        "input_schema": {"type": "object", "properties": {"course_id": {"type": "integer"}}, "required": ["course_id"]},
    },
    {
        "name": "cancel_quiz",
        "description": "Cancel (deactivate) a quiz. Destructive: first call returns a confirmation "
                       "prompt; call again with confirm=true to actually cancel.",
        "input_schema": {
            "type": "object",
            "properties": {"quiz_id": {"type": "integer"}, "confirm": {"type": "boolean"}},
            "required": ["quiz_id"],
        },
    },
    {
        "name": "review_study_plan",
        "description": "Approve or reject a pending study plan submitted by one of the instructor's students.",
        "input_schema": {
            "type": "object",
            "properties": {
                "plan_id": {"type": "integer"},
                "decision": {"type": "string", "enum": ["approved", "rejected"]},
                "note": {"type": "string"},
            },
            "required": ["plan_id", "decision"],
        },
    },
    {
        "name": "generate_summary_from_material",
        "description": "Have the AI read an already-uploaded course Material (lecture file) and write a summary of it.",
        "input_schema": {"type": "object", "properties": {"material_id": {"type": "integer"}}, "required": ["material_id"]},
    },
    {
        "name": "generate_quiz_from_material",
        "description": "Have the AI generate a quiz from an already-uploaded course Material. Unless the "
                       "instructor explicitly asks for essay-only or MCQ-only, default to a mix (e.g. num_mcq=5, "
                       "num_essay=0) so multiple-choice, auto-graded questions are included.",
        "input_schema": {
            "type": "object",
            "properties": {
                "material_id": {"type": "integer"},
                "topic_id": {"type": "integer"},
                "num_mcq": {"type": "integer"},
                "num_essay": {"type": "integer"},
                "title": {"type": "string"},
            },
            "required": ["material_id", "topic_id"],
        },
    },
    {
        "name": "generate_quiz_for_topic",
        "description": "Have the AI generate a 'recommended' quiz for a topic WITHOUT needing a specific "
                       "Material picked first -- it uses the course's existing materials as context, or a "
                       "generated primer if none exist. Use this (not add_question_to_quiz one-by-one) "
                       "whenever the instructor just says something like 'make a quiz for topic X' or 'give "
                       "students a recommended quiz'. Unless told otherwise, default to a mix (e.g. num_mcq=5, "
                       "num_essay=0) so it's not essay-only.",
        "input_schema": {
            "type": "object",
            "properties": {
                "course_id": {"type": "integer"},
                "topic_id": {"type": "integer"},
                "num_mcq": {"type": "integer"},
                "num_essay": {"type": "integer"},
                "title": {"type": "string"},
            },
            "required": ["course_id", "topic_id"],
        },
    },
    {
        "name": "add_question_to_quiz",
        "description": "Manually author ONE question (mcq or essay) on an existing quiz. Prefer "
                       "generate_quiz_for_topic or generate_quiz_from_material for building a whole "
                       "quiz -- only use this tool to hand-add or fix individual questions, and if you "
                       "do author MCQs by hand here, always give exactly 4 choices with exactly 1 correct.",
        "input_schema": {
            "type": "object",
            "properties": {
                "quiz_id": {"type": "integer"},
                "text": {"type": "string"},
                "question_type": {"type": "string", "enum": ["mcq", "essay"]},
                "choices": {
                    "type": "array",
                    "items": {"type": "object", "properties": {"text": {"type": "string"}, "is_correct": {"type": "boolean"}}},
                },
            },
            "required": ["quiz_id", "text", "question_type"],
        },
    },
    {
        "name": "grade_essay_response",
        "description": "Manually grade one student's essay/short-answer response (score 0.0-1.0).",
        "input_schema": {
            "type": "object",
            "properties": {
                "response_id": {"type": "integer"},
                "score": {"type": "number"},
                "feedback": {"type": "string"},
            },
            "required": ["response_id", "score"],
        },
    },
    {
        "name": "delete_material",
        "description": "Permanently delete an uploaded material (lecture video or course document), including its file. "
                       "Destructive: first call returns a confirmation prompt; call again with confirm=true to actually delete.",
        "input_schema": {
            "type": "object",
            "properties": {"material_id": {"type": "integer"}, "confirm": {"type": "boolean"}},
            "required": ["material_id"],
        },
    },
    {
        "name": "get_at_risk_students",
        "description": "List students in a course whose average topic mastery is below a threshold (default 50%), "
                       "for proactive intervention.",
        "input_schema": {
            "type": "object",
            "properties": {"course_id": {"type": "integer"}, "threshold": {"type": "number"}},
            "required": ["course_id"],
        },
    },
    {
        "name": "send_announcement",
        "description": "Send a notification/announcement to every student enrolled in a course.",
        "input_schema": {
            "type": "object",
            "properties": {"course_id": {"type": "integer"}, "message": {"type": "string"}},
            "required": ["course_id", "message"],
        },
    },
    {
        "name": "analyze_class_performance",
        "description": "AI insight for a course: hardest topic, class average score, how many students "
                       "are at risk, and a recommended action.",
        "input_schema": {"type": "object", "properties": {"course_id": {"type": "integer"}}, "required": ["course_id"]},
    },
    {
        "name": "generate_remedial_quiz",
        "description": "Generate a remedial quiz for a course's current hardest topic (by average "
                       "mastery), or a given topic_id if specified. Natural follow-up to "
                       "analyze_class_performance.",
        "input_schema": {
            "type": "object",
            "properties": {
                "course_id": {"type": "integer"},
                "topic_id": {"type": "integer"},
                "num_mcq": {"type": "integer"},
                "num_essay": {"type": "integer"},
            },
            "required": ["course_id"],
        },
    },
]

SYSTEM_PROMPT = (
    "You are the Instructor Agent of an LMS. You act ONLY on behalf of the "
    "currently authenticated instructor, and ONLY on courses they own. Use "
    "tools for every read or write -- never fabricate analytics or "
    "confirmations. Destructive tools (cancel_quiz, delete_material) require "
    "explicit confirmation from the instructor before you call them with "
    "confirm=true. When the instructor wants an AI/recommended quiz, prefer "
    "generate_quiz_from_material (if they named a file) or generate_quiz_for_topic "
    "(otherwise) over authoring questions one-by-one with add_question_to_quiz -- "
    "and default to including multiple-choice questions (num_mcq) unless the "
    "instructor specifically asks for essay-only. When the instructor asks how the "
    "class is doing or what to focus on, use analyze_class_performance, and offer "
    "generate_remedial_quiz as the natural follow-up. Keep replies short."
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
    understands every tool this agent exposes."""
    bound_impls = {name: (lambda _n=name, **p: call_tool(user, _n, **p)) for name in TOOL_FUNCS}

    if llm_available():
        try:
            return run_tool_loop(SYSTEM_PROMPT, TOOL_SPECS, bound_impls, message, history)
        except LLMUnavailableError as e:
            result = _fallback_router(user, message, bound_impls)
            if not result.get("tool_calls"):
                result["reply"] = f"\u26a0 {e}\n\n" + result["reply"]
            return result

    return _fallback_router(user, message, bound_impls)


def _fallback_router(user, message, bound_impls):
    """Offline mode: real intent routing, not a canned help string."""
    routed = nlu.route_instructor(user, message, bound_impls)
    if routed is not None:
        return routed
    return {"reply": nlu.INSTRUCTOR_HELP, "tool_calls": []}
