"""AI helpers that turn a Material's extracted text into a summary or a
quiz. Used by the Instructor Agent tools generate_summary_from_material /
generate_quiz_from_material (agents/tools.py) -- never called directly by
the Chatbot, keeping the read/write separation intact.

Uses Gemini when GEMINI_API_KEY is configured; otherwise -- or if the API
call itself fails for any reason (invalid/expired key, rate limit, network
issue) -- falls back to small deterministic heuristics so the feature still
works end-to-end instead of raising and surfacing "Internal tool error" to
the instructor. This module never lets a Gemini API exception escape.
"""
import json
import logging
import random
import re

import llm_client

logger = logging.getLogger(__name__)

# Sentence-ending punctuation, including Arabic question mark (؟) and Arabic
# comma-adjacent full stop usage, so splitting works for Arabic material too.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?؟])\s+")

# Unicode-aware "word" match: any run of letters that isn't a digit/underscore.
# Using [A-Za-z] here (the original bug) silently finds zero candidate words
# in Arabic (or any non-Latin) text, which is why MCQ generation was
# quietly dropping to zero questions and only essay prompts came out.
_WORD_RE = re.compile(r"[^\W\d_]{3,}", re.UNICODE)


def _llm_available():
    return llm_client.llm_available()


def _safe_complete(system, user_content, max_tokens):
    """Call the model (through the shared resilient client: model fallback
    chain + rate-limit circuit breaker) and return text, or None if it is
    unavailable for any reason. Callers fall back to a deterministic
    heuristic on None -- this function never raises."""
    if not _llm_available():
        return None
    try:
        return llm_client.generate_text(system, user_content, max_tokens=max_tokens)
    except llm_client.LLMUnavailableError as e:
        logger.warning("Model unavailable, using the offline heuristic instead: %s", e)
        return None
    except Exception as e:  # pragma: no cover - never let AI break a feature
        logger.warning("Unexpected model error, using the offline heuristic instead: %s", e)
        return None


# --------------------------------------------------------------------------
# Summary
# --------------------------------------------------------------------------


# Student-facing summary styles (see agents/tools.py:summarize_material). Each
# maps to a short system-prompt instruction plus a fallback strategy so the
# offline heuristic can approximate the same shape when no LLM is available.
SUMMARY_STYLES = {
    "bullet_points": "Write a clear, well-structured summary as 5-10 bullet points of the key concepts.",
    "detailed": "Write a detailed summary as 3-5 short paragraphs, explaining concepts and how they connect, not just listing them.",
    "tldr": "Write a very short TL;DR: 2-3 sentences capturing only the single most important takeaway.",
    "exam_prep": "Write the summary as a list of likely exam questions, each immediately followed by its concise answer (Q: ... / A: ...).",
    "eli5": "Explain the material as simply as possible, as if to someone with no background in the subject, using plain words and a short everyday-life analogy where it helps.",
}
DEFAULT_SUMMARY_STYLE = "bullet_points"


def summarize(text, course_title="", material_title="", style=DEFAULT_SUMMARY_STYLE):
    text = (text or "").strip()
    if not text:
        return "No extractable text was found in this file."
    style = style if style in SUMMARY_STYLES else DEFAULT_SUMMARY_STYLE

    result = _safe_complete(
        system=(
            "You summarize lecture material for a course LMS. "
            f"{SUMMARY_STYLES[style]} "
            "Do not invent facts not present in the text. Respond in the same "
            "language as the material."
        ),
        user_content=f"Material: {material_title}\n\n{text[:20000]}",
        max_tokens=500,
    )
    return result if result is not None else _fallback_summary(text, style)


def _fallback_summary(text, style=DEFAULT_SUMMARY_STYLE):
    sentences = _SENTENCE_SPLIT_RE.split(text)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 20]
    picked = sentences[:8] if sentences else [text[:400]]

    if style == "tldr":
        body = picked[0] if picked else text[:200]
    elif style == "exam_prep":
        body = "\n".join(f"Q: What should you know about this?\nA: {s}" for s in picked[:5])
    else:
        # "detailed" and "eli5" fall back to the same extractive bullets as
        # "bullet_points" offline -- genuinely rephrasing/simplifying text
        # needs the LLM, which is unavailable in this code path.
        body = "\n".join(f"- {s}" for s in picked)

    return (
        "Summary built by the offline engine (the AI model is not connected, or is "
        "over its quota right now):\n\n" + body
    )


def narrate_course_summary(course_title, facts):
    """Turn the hard numbers computed in agents/tools.py:generate_course_summary
    into a short, readable narrative. Falls back to the plain facts string if
    the LLM is unavailable -- so this never fails."""
    result = _safe_complete(
        system=(
            "You write short, encouraging course-progress summaries for an instructor "
            "dashboard, based only on the facts given. 3-5 sentences, plain language, "
            "no headers. Do not invent numbers not present in the facts."
        ),
        user_content=f"Course: {course_title}\nFacts: {facts}",
        max_tokens=300,
    )
    return result if result is not None else facts


def explain_topic(topic_name, course_title, context_text, mastery_percent=None):
    """AI-tutor explanation of a topic, grounded in uploaded course material
    when available. Falls back to a short generic explanation prompt if the
    LLM is unavailable, so the Student Agent's explain_topic tool never
    errors out even offline."""
    mastery_note = f" The student is currently at {mastery_percent:.0f}% mastery on this topic." if mastery_percent is not None else ""
    context_text = (context_text or "").strip()

    result = _safe_complete(
        system=(
            "You are a patient AI tutor inside an LMS. Explain the requested topic "
            "clearly and concisely for a student, using the provided course material "
            "as grounding when given. Use simple language, a short example if useful, "
            "and keep it under ~200 words. Respond in the same language the student's "
            "course material is written in."
        ),
        user_content=(
            f"Course: {course_title}\nTopic to explain: {topic_name}.{mastery_note}\n\n"
            f"Course material excerpt (may be empty):\n{context_text[:12000]}"
        ),
        max_tokens=500,
    )
    if result is not None:
        return result

    if context_text:
        sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(context_text) if len(s.strip()) > 20][:5]
        if sentences:
            return (
                f"Here's what the course material says about '{topic_name}' "
                f"(offline engine — the AI model is not connected or is over its quota):\n\n"
                + "\n".join(f"- {s}" for s in sentences)
            )
    return (
        f"I don't have an AI connection or material to draw on for '{topic_name}' right now. "
        f"Try the topic's quiz to see which parts need more review, or check the course materials tab."
    )


def narrate_student_progress(facts):
    """Turn the numbers from courses.services.compute_learning_dashboard into
    a short, encouraging paragraph for the student's Learning Analytics
    page. Falls back to the plain facts string if the LLM is unavailable."""
    result = _safe_complete(
        system=(
            "You write a short, encouraging 2-4 sentence progress note for a student's "
            "learning-analytics dashboard, based only on the facts given. Plain language, "
            "no headers, no bullet points. Do not invent numbers not present in the facts."
        ),
        user_content=f"Facts: {facts}",
        max_tokens=200,
    )
    return result if result is not None else facts


def narrate_class_insight(course_title, facts):
    """Turn the numbers from courses.services.compute_class_performance into
    a short instructor-facing insight that ends with one concrete
    recommended action (agents/tools.py:analyze_class_performance)."""
    result = _safe_complete(
        system=(
            "You write a short instructor-facing class-performance insight (3-4 sentences) "
            "based only on the facts given: name the hardest topic, the average score, how many "
            "students are at risk, and end with one concrete recommended action for the instructor. "
            "Do not invent numbers not present in the facts."
        ),
        user_content=f"Course: {course_title}\nFacts: {facts}",
        max_tokens=250,
    )
    return result if result is not None else facts


# --------------------------------------------------------------------------
# AI Tutor scoped to a single lecture/material (agents/tools.py:ask_about_material)
# --------------------------------------------------------------------------

_TUTOR_MODE_INSTRUCTIONS = {
    "ask": "Answer the student's question about this lecture as clearly and directly as possible.",
    "explain_simply": "Explain the main idea of this lecture in the simplest possible terms, as if to a complete beginner.",
    "give_example": "Give one clear, concrete example that illustrates a key concept from this lecture.",
    "explain_paragraph": "Explain, in plain language, what the specific passage below (from this lecture) means.",
    "hint": "The student is stuck on the problem/question given below and wants a HINT, not the final answer. "
            "Give one small nudge in the right direction without revealing the full solution.",
}


def tutor_on_material(material_text, course_title, material_title, mode, question="", paragraph=""):
    """AI Tutor answer scoped to exactly ONE lecture's text -- the core of
    the "AI Tutor built on the lecture" feature. Unlike explain_topic (which
    searches the whole course for relevant material), this is always
    grounded in the specific material the student is looking at."""
    material_text = (material_text or "").strip()
    instruction = _TUTOR_MODE_INSTRUCTIONS.get(mode, _TUTOR_MODE_INSTRUCTIONS["ask"])

    system = (
        "You are an AI tutor answering strictly about ONE specific lecture inside an LMS. "
        f"{instruction} Ground your answer in the lecture content provided; you may add a short "
        "clarifying example even if it isn't verbatim in the material, but never contradict the "
        "material or drift to a different topic. Keep the answer under ~180 words. Respond in the "
        "same language as the lecture material (or the student's question, if given)."
    )
    parts = [f"Lecture: {material_title} ({course_title})", f"Lecture content:\n{material_text[:12000]}"]
    if mode == "explain_paragraph" and paragraph:
        parts.append(f"Specific passage to explain:\n{paragraph}")
    if mode == "hint" and question:
        parts.append(f"What the student is stuck on:\n{question}")
    if question and mode not in ("explain_paragraph", "hint"):
        parts.append(f"Student's question / focus: {question}")

    result = _safe_complete(system=system, user_content="\n\n".join(parts), max_tokens=400)
    if result is not None:
        return result

    if material_text:
        sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(material_text) if len(s.strip()) > 20][:5]
        if sentences:
            return (
                "(offline engine — the AI model is not connected or is over its quota)\n\n"
                "Here's the relevant part of the lecture:\n\n" + "\n".join(f"- {s}" for s in sentences)
            )
    return "I don't have an AI connection or lecture text to draw on right now."


# --------------------------------------------------------------------------
# Adaptive practice: single difficulty-aware question generation
# (agents/tools.py: start_adaptive_practice / submit_adaptive_answer)
# --------------------------------------------------------------------------

_DIFFICULTY_HINTS = {
    "easy": "Make it straightforward, testing basic recall or a simple definition.",
    "medium": "Make it moderately challenging, testing applied understanding, not just recall.",
    "hard": "Make it challenging: an edge case, a comparison, or a multi-step reasoning question.",
}


def generate_single_question(context_text, difficulty="medium"):
    """One MCQ, deliberately harder/easier per `difficulty`, for the
    adaptive-practice loop. Falls back to the same offline generator used
    elsewhere (which cannot vary difficulty without the LLM -- a known,
    documented limitation of running without GEMINI_API_KEY)."""
    context_text = (context_text or "").strip()
    if not context_text:
        return None
    hint = _DIFFICULTY_HINTS.get(difficulty, _DIFFICULTY_HINTS["medium"])

    result = _safe_complete(
        system=(
            "You write ONE multiple-choice practice question strictly based on the material given. "
            f"{hint} Respond with ONLY a JSON object, no prose, no markdown fences: "
            '{"text": "...", "choices": [{"text": "...", "is_correct": true|false}, '
            '... exactly 4 choices, exactly 1 correct]}. Respond in the same language as the material.'
        ),
        user_content=f"Material:\n{context_text[:12000]}",
        max_tokens=500,
    )
    if result is not None:
        raw = result.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        try:
            data = json.loads(raw)
            if data.get("text") and data.get("choices"):
                return data
        except (json.JSONDecodeError, TypeError, AttributeError):
            logger.warning("Could not parse single-question JSON; falling back to heuristic.")

    generated = _fallback_generate(context_text, num_mcq=1, num_essay=0)
    return generated[0] if generated else None


def synthesize_topic_primer(topic_name, course_title):
    """When a course has no readable material yet, generate a short
    informational passage about the topic so the quiz generator (both the
    LLM prompt and the offline fallback, which needs real sentences to
    blank out) has something concrete to write MCQs from, instead of
    defaulting to essay-only questions for lack of source text."""
    result = _safe_complete(
        system=(
            "Write a short, factual, textbook-style passage (5-8 sentences) introducing the given "
            "topic, suitable as source material for a student quiz. Plain sentences, no headers, no "
            "questions. Respond in the same language as the topic name."
        ),
        user_content=f"Course: {course_title}\nTopic: {topic_name}",
        max_tokens=400,
    )
    if result:
        return result
    return (
        f"{topic_name} is a key topic in {course_title}. Understanding {topic_name} requires reviewing "
        f"its core definitions, how it's typically applied, and common mistakes students make with it. "
        f"Students should be able to explain {topic_name} in their own words and recognize it in examples."
    )


# --------------------------------------------------------------------------
# Quiz generation
# --------------------------------------------------------------------------

def generate_quiz_questions(text, num_mcq=5, num_essay=0):
    """Returns a list of dicts:
      {"type": "mcq", "text": "...", "choices": [{"text": "...", "is_correct": bool}, ...]}
      {"type": "essay", "text": "..."}
    """
    text = (text or "").strip()
    if not text:
        return []

    result = _llm_generate(text, num_mcq, num_essay) or []
    got_mcq = sum(1 for q in result if q.get("type") == "mcq")
    got_essay = sum(1 for q in result if q.get("type") == "essay")

    # Guarantee the requested MCQ/essay split even if the LLM didn't fully
    # honour it -- in practice models sometimes return essay-only questions
    # (especially on short/ambiguous material), which silently drops MCQs.
    # Top up any shortfall with the deterministic offline generator so the
    # instructor always gets the mix they asked for.
    mcq_shortfall = max(num_mcq - got_mcq, 0)
    essay_shortfall = max(num_essay - got_essay, 0)
    if result and (mcq_shortfall or essay_shortfall):
        result = result + _fallback_generate(text, mcq_shortfall, essay_shortfall)

    return result if result else _fallback_generate(text, num_mcq, num_essay)


def _llm_generate(text, num_mcq, num_essay):
    schema_hint = (
        'Respond with ONLY a JSON array, no prose, no markdown fences. Each item: '
        '{"type": "mcq", "text": "...", "choices": [{"text": "...", "is_correct": true|false}, '
        '... exactly 4 choices, exactly 1 correct]} '
        'or {"type": "essay", "text": "..."} for open-ended questions.'
    )
    prompt = (
        f"Based ONLY on the material below, write exactly {num_mcq} multiple-choice questions "
        f"and exactly {num_essay} essay/short-answer questions that test understanding of it. "
        f"It is critical that you produce exactly {num_mcq} items with \"type\": \"mcq\" -- "
        f"do not substitute essay questions for the requested MCQs, even if the material is short; "
        f"reuse or lightly vary details from the material to reach the count if needed. "
        f"Write the questions in the same language as the material. {schema_hint}\n\nMaterial:\n{text[:20000]}"
    )
    raw = _safe_complete(
        system="You are an instructional designer generating quiz questions strictly from provided material.",
        user_content=prompt,
        max_tokens=2000,
    )
    if raw is None:
        return None
    raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        data = json.loads(raw)
        return [q for q in data if q.get("type") in ("mcq", "essay") and q.get("text")]
    except (json.JSONDecodeError, TypeError):
        logger.warning("Could not parse LLM quiz-generation output as JSON; falling back to heuristic.")
        return None


def _fallback_generate(text, num_mcq, num_essay):
    """Crude offline generator: turns sentences into fill-in-the-blank MCQs by
    blanking a distinctive word, and lifts a couple of sentences as essay
    prompts. Not high quality -- a functional stand-in when no API key is
    set/valid. Unicode-aware so it also works on Arabic (or any other
    script) material, not just English."""
    sentences = _SENTENCE_SPLIT_RE.split(text)
    sentences = [s.strip() for s in sentences if len(s.split()) >= 4]
    random.shuffle(sentences)

    words_pool = _WORD_RE.findall(text)
    words_pool = list(set(words_pool)) or ["concept"]

    used_sentences = set()
    questions = []
    for s in sentences:
        if len(questions) >= num_mcq:
            break
        candidates = _WORD_RE.findall(s)
        if not candidates:
            continue
        answer = random.choice(candidates)
        blanked = re.sub(rf"\b{re.escape(answer)}\b", "_____", s, count=1)
        if blanked == s:
            # answer didn't have clean word boundaries (common with Arabic
            # diacritics/joiners) -- fall back to a plain string replace once.
            blanked = s.replace(answer, "_____", 1)
        distractor_pool = [w for w in words_pool if w.lower() != answer.lower()]
        if not distractor_pool:
            continue
        distractors = random.sample(distractor_pool, k=min(3, len(distractor_pool)))
        choices = [{"text": answer, "is_correct": True}] + [{"text": d, "is_correct": False} for d in distractors]
        random.shuffle(choices)
        questions.append({"type": "mcq", "text": f"Fill in the blank: {blanked}", "choices": choices})
        used_sentences.add(s)

    remaining = [s for s in sentences if s not in used_sentences]
    for s in remaining[:num_essay]:
        questions.append({"type": "essay", "text": f"Explain, in your own words: \"{s}\""})
        used_sentences.add(s)

    return questions
