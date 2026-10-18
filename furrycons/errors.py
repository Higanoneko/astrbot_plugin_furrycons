class UserError(Exception):
    """A diagnostic that may be shown to a chat user without leaking credentials."""


class SourceError(UserError):
    pass


class RenderError(UserError):
    pass
