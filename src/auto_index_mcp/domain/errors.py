from __future__ import annotations


class ServiceError(RuntimeError):
    def __init__(self, code: str, message: str, hint: str = "", **details) -> None:
        super().__init__(message)
        self.code = code
        self.hint = hint
        self.details = details


class NotEnabledError(ServiceError):
    def __init__(self) -> None:
        super().__init__("not-enabled", "auto-index is not enabled",
                         "call auto_index_enable(root_path=<absolute project path>) first")
