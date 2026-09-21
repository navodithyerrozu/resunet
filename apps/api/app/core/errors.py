"""Domain exceptions and their HTTP mapping.

Route handlers raise domain errors; a single exception handler in `main.py`
translates them to responses. This keeps HTTP concerns out of the service
layer, so the same services can be called from the ARQ workers -- which have
no notion of a status code -- without change.
"""

from __future__ import annotations

from typing import Any

from fastapi import status


class ResuNetError(Exception):
    """Base for all domain errors."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "internal_error"
    message: str = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.message
        self.details = details or {}
        super().__init__(self.message)

    def to_payload(self) -> dict[str, Any]:
        """Stable error envelope. The frontend switches on `code`, never on prose."""
        body: dict[str, Any] = {"error": {"code": self.code, "message": self.message}}
        if self.details:
            body["error"]["details"] = self.details
        return body


class NotFoundError(ResuNetError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"
    message = "The requested resource does not exist."


class PermissionDeniedError(ResuNetError):
    """Ownership check failure.

    Returned as 404 rather than 403 on purpose: a 403 confirms the resource
    exists, which leaks the id space to anyone probing it.
    """

    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"
    message = "The requested resource does not exist."


class UnauthorizedError(ResuNetError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "unauthorized"
    message = "Authentication required."


class ConflictError(ResuNetError):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"
    message = "The resource already exists or conflicts with current state."


class ValidationFailedError(ResuNetError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "validation_failed"
    message = "The submitted data is invalid."


class RateLimitedError(ResuNetError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "rate_limited"
    message = "Too many requests. Try again shortly."


class XYZComplianceError(ValidationFailedError):
    """A bullet could not be made XYZ-compliant within the repair budget.

    Surfaced to the user rather than swallowed: silently persisting a
    non-compliant bullet would defeat the product's core promise, so the
    correct behaviour is to say so and ask for the missing metric.
    """

    code = "xyz_non_compliant"
    message = "This bullet is missing a measurable outcome."


class RenderFailedError(ResuNetError):
    status_code = status.HTTP_502_BAD_GATEWAY
    code = "render_failed"
    message = "The LaTeX compile did not produce a PDF."


class AutomationBlockedError(ResuNetError):
    """The target site blocked an automated submission.

    A distinct error because the correct response is operational -- back off
    that board, fall back to assisted mode -- not a generic retry.
    """

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "automation_blocked"
    message = "The job board blocked automated submission. Try assisted mode."


class ExternalServiceError(ResuNetError):
    status_code = status.HTTP_502_BAD_GATEWAY
    code = "external_service_error"
    message = "An upstream service failed."
