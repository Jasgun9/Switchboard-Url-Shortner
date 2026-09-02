"""One error shape for the whole API:

    {"error": {"code": "ALIAS_ALREADY_EXISTS", "message": "...", "details": {...}}}
"""

import logging

from django.conf import settings
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404, JsonResponse
from rest_framework import exceptions, status
from rest_framework.views import exception_handler as drf_exception_handler

log = logging.getLogger(__name__)

CODES_BY_EXCEPTION = {
    Http404: "NOT_FOUND",
    DjangoPermissionDenied: "PERMISSION_DENIED",
    exceptions.ParseError: "MALFORMED_REQUEST",
    exceptions.AuthenticationFailed: "AUTHENTICATION_FAILED",
    exceptions.NotAuthenticated: "AUTHENTICATION_REQUIRED",
    exceptions.PermissionDenied: "PERMISSION_DENIED",
    exceptions.NotFound: "NOT_FOUND",
    exceptions.MethodNotAllowed: "METHOD_NOT_ALLOWED",
    exceptions.UnsupportedMediaType: "UNSUPPORTED_MEDIA_TYPE",
    exceptions.Throttled: "RATE_LIMITED",
    exceptions.ValidationError: "VALIDATION_ERROR",
}


class APIError(exceptions.APIException):
    # Raise for the domain-specific failures the API names explicitly.

    def __init__(self, code, message, status_code=status.HTTP_400_BAD_REQUEST):
        self.error_code = code
        self.status_code = status_code
        super().__init__(message)


def error_body(code, message, details=None):
    body = {"error": {"code": code, "message": message}}
    if details:
        body["error"]["details"] = details
    return body


def _message(exc, response):
    # Http404 and PermissionDenied arrive without .detail, but DRF has already
    # put a message in the body.
    detail = getattr(exc, "detail", None)
    if detail is None and isinstance(response.data, dict):
        detail = response.data.get("detail", "")

    if isinstance(detail, list) and detail:
        return str(detail[0])
    if isinstance(detail, dict):
        return "The request could not be processed."
    return str(detail)


def exception_handler(exc, context):
    response = drf_exception_handler(exc, context)

    if response is None:
        # Anything DRF didn't recognise is a bug on our side. Log the traceback,
        # return something that gives nothing away.
        log.exception("unhandled API exception in %s", context.get("view"))
        if settings.DEBUG:
            return None
        return JsonResponse(
            error_body("INTERNAL_ERROR", "An unexpected error occurred."),
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    code = getattr(exc, "error_code", None) or CODES_BY_EXCEPTION.get(type(exc), "ERROR")

    if isinstance(exc, exceptions.ValidationError):
        response.data = error_body("VALIDATION_ERROR", "The request contains invalid fields.", response.data)
    else:
        response.data = error_body(code, _message(exc, response))

    if isinstance(exc, exceptions.Throttled) and exc.wait:
        response["Retry-After"] = str(int(exc.wait))

    return response
