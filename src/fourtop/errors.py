"""Stable CLI error categories shared by the UI and services."""


class FourtopError(Exception):
    def __init__(self, message: str, code: int = 1):
        super().__init__(message)
        self.code = code


class Missing(FourtopError):
    def __init__(self, message: str):
        super().__init__(message, 3)


class Conflict(FourtopError):
    def __init__(self, message: str):
        super().__init__(message, 4)


class Dependency(FourtopError):
    def __init__(self, message: str):
        super().__init__(message, 5)


class Unavailable(FourtopError):
    def __init__(self, message: str):
        super().__init__(message, 6)
