import functools
from .models import AgentActionLog
from .exceptions import ToolPermissionError, ToolValidationError, ConfirmationRequired


def agent_tool(agent_name, destructive=False):
    """Wraps a tool function so that:
      1. Every call (success, denial, error, or pending-confirmation) is
         written to AgentActionLog -- this is the audit trail the SRS's
         'security' NFR asks for.
      2. Exceptions are turned into a uniform {"ok": False, "error": ...}
         shape instead of leaking stack traces to the agent/chat layer.
      3. Destructive tools require params.get("confirm") is True, otherwise
         they short-circuit with a confirmation prompt on the first call.

    A tool function's signature is always: tool(user, **params) -> dict
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(user, **params):
            tool_name = func.__name__

            if destructive and not params.get("confirm"):
                prompt = f"'{tool_name}' is a destructive action. Call again with confirm=true to proceed."
                AgentActionLog.objects.create(
                    user=user, agent=agent_name, tool_name=tool_name,
                    params=_safe(params), result={"prompt": prompt},
                    status=AgentActionLog.Status.PENDING_CONFIRM,
                )
                return {"ok": False, "requires_confirmation": True, "message": prompt}

            try:
                data = func(user, **params)
                AgentActionLog.objects.create(
                    user=user, agent=agent_name, tool_name=tool_name,
                    params=_safe(params), result=_safe(data),
                    status=AgentActionLog.Status.SUCCESS,
                )
                return {"ok": True, "data": data}
            except ToolPermissionError as e:
                AgentActionLog.objects.create(
                    user=user, agent=agent_name, tool_name=tool_name,
                    params=_safe(params), result={"error": str(e)},
                    status=AgentActionLog.Status.DENIED,
                )
                return {"ok": False, "error": str(e), "code": "PERMISSION_DENIED"}
            except ToolValidationError as e:
                AgentActionLog.objects.create(
                    user=user, agent=agent_name, tool_name=tool_name,
                    params=_safe(params), result={"error": str(e)},
                    status=AgentActionLog.Status.ERROR,
                )
                return {"ok": False, "error": str(e), "code": "VALIDATION_ERROR"}
            except Exception as e:  # pragma: no cover - safety net
                AgentActionLog.objects.create(
                    user=user, agent=agent_name, tool_name=tool_name,
                    params=_safe(params), result={"error": str(e)},
                    status=AgentActionLog.Status.ERROR,
                )
                return {"ok": False, "error": "Internal tool error.", "code": "ERROR"}

        wrapper.tool_name = func.__name__
        wrapper.is_destructive = destructive
        return wrapper

    return decorator


def _safe(obj):
    """Best-effort conversion of tool params/results into JSON-serialisable data."""
    if isinstance(obj, dict):
        return {k: _safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_safe(v) for v in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)
