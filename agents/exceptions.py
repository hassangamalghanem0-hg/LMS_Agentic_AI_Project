class ToolPermissionError(Exception):
    """Raised when the caller is not allowed to run a tool (RBAC failure)."""


class ToolValidationError(Exception):
    """Raised when tool arguments are invalid or the target object doesn't exist."""


class ConfirmationRequired(Exception):
    """Raised by destructive tools on their first call. Carries a human
    readable prompt; the caller must resend the same call with confirm=True."""

    def __init__(self, prompt, echo=None):
        super().__init__(prompt)
        self.prompt = prompt
        self.echo = echo or {}
