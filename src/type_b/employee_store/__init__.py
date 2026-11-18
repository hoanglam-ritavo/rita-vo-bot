"""Opt-in readonly HRM access; no automatic API, model or database activation."""
from .config import EmployeeSourceSettings
from .contracts import EmployeeProfile, ManagerSummary, ManagerResult, EmployeeStoreReader
from .errors import EmployeeSourceError, EmployeeValidationError
from .service import EmployeeSourceService

__all__ = ['EmployeeSourceSettings', 'EmployeeProfile', 'EmployeeSourceService',
           'EmployeeSourceError', 'EmployeeValidationError', 'ManagerSummary', 'ManagerResult', 'EmployeeStoreReader']
