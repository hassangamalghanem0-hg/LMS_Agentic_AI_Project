"""One resilient Gemini transport, shared by every module that talks to the
model (agents/llm_orchestrator.py, courses/ai_generation.py,
chatbot/services.py).

Why this file exists
--------------------
The three call sites each had their own copy of "try once, retry twice with a
flat 1.5s backoff, then give up". On a free-tier key that is exactly the wrong
behaviour:

  * A 429 from Google is almost never "try again in 1.5 seconds". It is either
    a per-minute limit (retry in ~30-60s) or a per-day quota (retry tomorrow).
    Retrying it twice just made every single chat message hang for ~4.5s and
    *still* drop to basic mode.
  * Once the daily quota is gone, every following request paid that same 4.5s
    penalty. That is what made the demo feel broken rather than degraded.
  * A single hard-coded model name means one exhausted quota takes the whole
    AI layer down, even though other Gemini models have separate quota pools.

So this module adds three things on top of the raw SDK call:

  1. **A model chain.** Try the configured model first; on 429/404/5xx move to
     the next model in the chain (each Gemini model has its own quota bucket,
     so a flash-lite model usually still answers when flash is exhausted).
  2. **A circuit breaker.** A model that returned 429 is put on cooldown for
     the delay Google asked for (or 60s by default) and skipped entirely until
     then. When every model is cooling, we raise *immediately* -- so the
     rule-based fallback answers in milliseconds instead of after a 5s stall.
  3. **Correct error classification.** 401/403 (bad key) never retries and
     never wastes the other models' attempts. 404 (model retired -- Gemini
     model names change often) permanently drops that name from the chain for
     the life of the process and moves on.

Everything raises LLMUnavailableError on failure; no caller ever sees a raw
SDK exception, and no caller ever sees a 500.
"""
import logging
import os
import random
import re
import threading
import time

try:
    from google import genai
    from google.genai import types
    from google.genai import errors as genai_errors
except ImportError:  # pragma: no cover - SDK not installed
    genai = None
    types = None
    genai_errors = None

logger = logging.getLogger(__name__)


class LLMUnavailableError(Exception):
    """The model could not be reached (missing/invalid key, quota exhausted,
    network issue, every model in the chain down). Callers catch this and fall
    back to the offline rule-based router instead of raising a 500."""


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

# Primary model. Gemini model names change every few months, which is exactly
# why this is not the only one we try.
DEFAULT_MODEL = "gemini-3.8-flash"

# Ordered fallbacks, tried when the primary is rate-limited or retired. These
# are deliberately spread across generations and sizes: each model name has
# its own free-tier quota bucket, and the lite models have far higher
# request-per-day allowances than the flagship flash models.
DEFAULT_FALLBACK_MODELS = [
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
]

# How long to skip a model after it rate-limits us, when Google's error
# doesn't carry an explicit retry delay.
DEFAULT_COOLDOWN_SECONDS = 60.0

# Cap: never honour a retry delay longer than this by sleeping. Anything
# bigger means "daily quota" -- we cool the model down and move on instead.
MAX_INLINE_SLEEP_SECONDS = 4.0

_RETRYABLE_SERVER_CODES = {500, 502, 503, 504}
_RATE_LIMIT_CODE = 429
_FATAL_AUTH_CODES = {401, 403}
_MODEL_GONE_CODES = {404}


def _env_list(name):
    raw = os.environ.get(name, "")
    return [m.strip() for m in raw.split(",") if m.strip()]


def model_chain():
    """The ordered list of model names to try, minus any we've learned are
    retired, minus any currently on cooldown."""
    primary = os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)
    fallbacks = _env_list("GEMINI_FALLBACK_MODELS") or list(DEFAULT_FALLBACK_MODELS)

    chain = []
    for name in [primary] + fallbacks:
        if name and name not in chain:
            chain.append(name)
    return chain


def offline_mode():
    """Set AI_OFFLINE_MODE=1 to skip the API entirely and run purely on the
    rule-based routers. Useful for a guaranteed-stable demo/defence run where
    a flaky network or an exhausted quota would be worse than no AI at all."""
    return os.environ.get("AI_OFFLINE_MODE", "").strip().lower() in ("1", "true", "yes", "on")


def llm_available():
    """True if we *could* call the API: SDK installed, key present, not forced
    offline. Does not guarantee the call will succeed -- that's what the
    fallbacks are for."""
    if offline_mode():
        return False
    return bool(genai) and bool(os.environ.get("GEMINI_API_KEY", "").strip())


# --------------------------------------------------------------------------
# Circuit breaker state (process-local, thread-safe)
# --------------------------------------------------------------------------

_lock = threading.Lock()
_cooldown_until = {}   # model name -> monotonic timestamp it becomes usable again
_retired_models = set()  # model names that returned 404 (wrong/removed name)
_last_good_model = None  # tried first next time, so we don't re-walk the chain


def _now():
    return time.monotonic()


def _cool_down(model, seconds):
    seconds = max(1.0, float(seconds))
    with _lock:
        _cooldown_until[model] = _now() + seconds
    logger.warning("Gemini model %s rate-limited; skipping it for %.0fs", model, seconds)


def _is_cooling(model):
    with _lock:
        until = _cooldown_until.get(model)
    return bool(until and until > _now())


def _retire(model):
    with _lock:
        _retired_models.add(model)
    logger.warning("Gemini model %s not found (404); dropping it from the chain", model)


def _usable_models():
    """Chain order, best-known model first, with retired and cooling names
    removed. Empty means 'everything is down right now'."""
    chain = [m for m in model_chain() if m not in _retired_models]
    with _lock:
        good = _last_good_model
    if good and good in chain:
        chain.remove(good)
        chain.insert(0, good)
    return [m for m in chain if not _is_cooling(m)]


def cooldown_status():
    """Diagnostics for the README/debug view: what's currently blocked."""
    with _lock:
        now = _now()
        return {
            "retired": sorted(_retired_models),
            "cooling": {m: round(t - now, 1) for m, t in _cooldown_until.items() if t > now},
            "last_good_model": _last_good_model,
        }


# --------------------------------------------------------------------------
# Error inspection
# --------------------------------------------------------------------------

_RETRY_DELAY_RE = re.compile(r"retry[ _-]?delay['\"]?\s*[:=]\s*['\"]?(\d+(?:\.\d+)?)s?", re.I)


def _error_code(exc):
    code = getattr(exc, "code", None)
    if isinstance(code, int):
        return code
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    # Some SDK versions only put the status in the message.
    m = re.search(r"\b(4\d\d|5\d\d)\b", str(exc))
    return int(m.group(1)) if m else None


def _suggested_retry_delay(exc):
    """Google returns a RetryInfo with a retryDelay on 429s. Honour it when it
    is short enough to wait out inline; otherwise it tells us how long to put
    the model on cooldown."""
    m = _RETRY_DELAY_RE.search(str(exc))
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return None


def _is_daily_quota(exc):
    text = str(exc).lower()
    return "perday" in text.replace("_", "").replace(" ", "") or "per day" in text


# --------------------------------------------------------------------------
# The call
# --------------------------------------------------------------------------

def _client():
    try:
        return genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    except Exception as e:
        raise LLMUnavailableError(f"Could not initialise the Gemini client: {e}") from e


def generate(contents, config, attempts_per_model=2):
    """Call generate_content against the first model in the chain that works.

    contents: list of types.Content (or a plain string -- the SDK accepts both)
    config:   a types.GenerateContentConfig (model-independent, so the same
              object is reused across the whole chain)

    Returns the raw SDK response. Raises LLMUnavailableError if no model in
    the chain could serve the request.
    """
    if offline_mode():
        raise LLMUnavailableError("AI is running in offline mode (AI_OFFLINE_MODE is set).")
    if not genai:
        raise LLMUnavailableError("The google-genai package isn't installed (pip install -r requirements.txt).")
    if not os.environ.get("GEMINI_API_KEY", "").strip():
        raise LLMUnavailableError("The AI connection isn't configured (GEMINI_API_KEY is missing).")

    candidates = _usable_models()
    if not candidates:
        # Every model is cooling or retired. Fail instantly -- making the user
        # wait just to reach the same fallback is strictly worse than reaching
        # it immediately.
        raise LLMUnavailableError(
            "The AI is over its request quota for the moment, so I'm answering from "
            "the built-in engine instead."
        )

    client = _client()
    last_error = None

    for model in candidates:
        for attempt in range(attempts_per_model):
            try:
                response = client.models.generate_content(model=model, contents=contents, config=config)
                with _lock:
                    globals()["_last_good_model"] = model
                    _cooldown_until.pop(model, None)
                return response
            except Exception as e:
                last_error = e
                code = _error_code(e)

                if genai_errors and not isinstance(e, genai_errors.APIError) and code is None:
                    # Connection reset, DNS failure, timeout -- worth one quick
                    # retry on the same model, then move on.
                    if attempt + 1 < attempts_per_model:
                        time.sleep(0.8 + random.random() * 0.4)
                        continue
                    logger.warning("Gemini transport error on %s: %s", model, e)
                    break

                if code in _FATAL_AUTH_CODES:
                    # A bad key is bad for every model -- stop the whole walk.
                    raise LLMUnavailableError(
                        "The Gemini API key was rejected (check GEMINI_API_KEY in your .env)."
                    ) from e

                if code in _MODEL_GONE_CODES:
                    _retire(model)
                    break

                if code == _RATE_LIMIT_CODE:
                    delay = _suggested_retry_delay(e)
                    if (delay is not None and delay <= MAX_INLINE_SLEEP_SECONDS
                            and not _is_daily_quota(e) and attempt + 1 < attempts_per_model):
                        # Short per-minute throttle: waiting it out is cheaper
                        # than switching models.
                        time.sleep(delay + random.random() * 0.3)
                        continue
                    _cool_down(model, delay or DEFAULT_COOLDOWN_SECONDS)
                    break  # different model = different quota bucket

                if code in _RETRYABLE_SERVER_CODES:
                    if attempt + 1 < attempts_per_model:
                        time.sleep(0.9 * (attempt + 1) + random.random() * 0.4)
                        continue
                    _cool_down(model, 20)
                    break

                # 400 and anything else unclassified: our request is wrong for
                # this model, not transient. Try the next model once, then stop.
                logger.warning("Gemini call failed on %s (code %s): %s", model, code, e)
                break

    logger.warning("Every Gemini model in the chain failed; using the offline engine. Last error: %s", last_error)
    raise LLMUnavailableError(
        "The AI is busy or over quota right now, so I'm answering from the built-in engine instead."
    )


def generate_text(system, user_content, max_tokens=500):
    """Convenience wrapper for the simple 'one prompt in, text out' callers
    (summaries, explanations, the chatbot). Returns the text, or raises
    LLMUnavailableError."""
    config = types.GenerateContentConfig(
        system_instruction=system,
        max_output_tokens=max_tokens,
    )
    contents = [types.Content(role="user", parts=[types.Part.from_text(text=user_content)])]
    response = generate(contents, config)
    if not response.candidates:
        raise LLMUnavailableError("The model returned no usable response (it may have been filtered).")
    text = "".join(p.text for p in (response.candidates[0].content.parts or []) if p.text)
    if not text.strip():
        raise LLMUnavailableError("The model returned an empty response.")
    return text
