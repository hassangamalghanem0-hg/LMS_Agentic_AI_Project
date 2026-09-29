def agent_result(request):
    """Exposes the last successful agent-tool result (stashed by
    agents/views.py:_flash) on whichever page the action's redirect lands on
    -- the student/instructor dashboard, or (via the "next" full-path
    redirect) a full-screen page like Analytics or a Study Plan detail.
    Popped, not just read, so -- like Django's own messages framework -- it
    is shown exactly once."""
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {}
    session = getattr(request, "session", None)
    if session is None:
        return {}
    return {"agent_result": session.pop("agent_result", None)}
