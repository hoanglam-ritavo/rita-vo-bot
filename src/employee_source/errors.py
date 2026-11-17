"""Stable diagnostics: never include SQL, parameters or source values."""


class EmployeeSourceError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(f'Employee source unavailable ({code}).')


class EmployeeValidationError(EmployeeSourceError):
    pass
