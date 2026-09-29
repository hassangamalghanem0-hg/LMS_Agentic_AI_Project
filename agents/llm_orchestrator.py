"""Thin wrapper around the Gemini API's tool-use ("function calling") loop.

Originally built on Anthropic's Claude API; switched to Google's Gemini API
(via the google-genai SDK) because the project's Anthropic credits ran out.
Only this file, courses/ai_generation.py and chatbot/services.py know which
provider is in use -- every agent tool (agents/tools.py) is provider-agnostic.

All transport concerns (retries, the multi-model fallback chain, the
rate-limit circuit breaker, error classification) now live in
agents/llm_client.py, so this file is only about the tool-calling loop.

If the model can't be reached -- no key, bad key, quota exhausted, network
down -- this raises LLMUnavailableError and the agents fall back to the
offline rule-based router in agents/nlu.py, which understands every tool the
agent exposes. The project therefore runs end-to-end with or without AI.
"""
import logging

from llm_client import (  # re-exported: existing imports of these keep working
    LLMUnavailableError,
    llm_available,
    generate,
    types,
)

logger = logging.getLogger(__name__)

__all__ = ["LLMUnavailableError", "llm_available", "run_tool_loop"]


def _to_gemini_tools(tool_specs):
    """Our TOOL_SPECS are already plain JSON Schema (name/description/input_schema),
    written for Anthropic's API. Gemini's FunctionDeclaration accepts that
    same JSON Schema shape via parameters_json_schema, so no per-tool
    rewriting is needed when switching providers."""
    declarations = [
        types.FunctionDeclaration(
            name=t["name"],
            description=t["description"],
            parameters_json_schema=t["input_schema"],
        )
        for t in tool_specs
    ]
    return [types.Tool(function_declarations=declarations)]


def run_tool_loop(system_prompt, tool_specs, tool_impls, user_message, history=None, max_iters=4):
    """
    tool_specs: list of {"name", "description", "input_schema"} dicts (Anthropic-style JSON schema)
    tool_impls: dict[name] -> callable(**params) -> dict   (already bound to `user`)
    Returns: {"reply": str, "tool_calls": [ {name, input, output}, ... ]}

    Raises LLMUnavailableError if no model in the chain can serve the request
    -- callers should catch this and fall back to the offline router.
    """
    if not llm_available():
        raise LLMUnavailableError("The AI connection isn't configured (GEMINI_API_KEY is missing).")

    gemini_tools = _to_gemini_tools(tool_specs)
    config = types.GenerateContentConfig(system_instruction=system_prompt, tools=gemini_tools)

    contents = list(history or [])
    contents.append(types.Content(role="user", parts=[types.Part.from_text(text=user_message)]))
    tool_calls_log = []

    for _ in range(max_iters):
        # generate() walks the model chain and raises LLMUnavailableError once
        # every model is exhausted -- which the caller turns into offline mode.
        response = generate(contents, config)

        if not response.candidates:
            if tool_calls_log:
                # Tools already ran and produced real data; don't throw that
                # away just because the final wording came back empty.
                return {"reply": _describe_calls(tool_calls_log), "tool_calls": tool_calls_log}
            raise LLMUnavailableError("The model returned no response (it may have been filtered).")

        candidate = response.candidates[0]
        parts = candidate.content.parts or []
        function_calls = [p.function_call for p in parts if p.function_call]

        if not function_calls:
            text = "".join(p.text for p in parts if p.text)
            if not text.strip() and tool_calls_log:
                text = _describe_calls(tool_calls_log)
            return {"reply": text, "tool_calls": tool_calls_log}

        contents.append(candidate.content)  # the model's turn, exactly as returned
        response_parts = []
        for fc in function_calls:
            impl = tool_impls.get(fc.name)
            args = dict(fc.args) if fc.args else {}
            if impl is None:
                output = {"ok": False, "error": f"Unknown tool {fc.name}"}
            else:
                output = impl(**args)
            tool_calls_log.append({"name": fc.name, "input": args, "output": output})
            response_parts.append(types.Part.from_function_response(name=fc.name, response=output))
        contents.append(types.Content(role="user", parts=response_parts))

    return {"reply": "I reached my step limit while working on that.", "tool_calls": tool_calls_log}


def _describe_calls(tool_calls_log):
    """Last-resort wording when the model ran tools but produced no prose --
    better than showing the student an empty bubble after a real action."""
    from . import nlu
    lines = [nlu.render_result(c["name"], c["output"]) for c in tool_calls_log]
    return "\n\n".join(line for line in lines if line) or "Done."
