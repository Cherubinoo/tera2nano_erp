from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError
from django.db.models import ProtectedError
from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.response import Response
from rest_framework.views import exception_handler


def _flatten(exc):
    if hasattr(exc, "error_dict"):
        return exc.message_dict
    return {"non_field_errors": exc.messages}


def api_exception_handler(exc, context):
    if isinstance(exc, DjangoValidationError):
        return Response(_flatten(exc), status=status.HTTP_400_BAD_REQUEST)
    if isinstance(exc, ProtectedError):
        return Response(
            {
                "detail": "This record is referenced by issued documents or other records and cannot be "
                "deleted. Deactivate it instead.",
                "code": "protected",
            },
            status=status.HTTP_409_CONFLICT,
        )
    if isinstance(exc, IntegrityError):
        return Response(
            {"detail": "This change conflicts with an existing record (a duplicate or overlapping value).",
             "code": "conflict"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    response = exception_handler(exc, context)
    if response is not None and isinstance(exc, APIException) and isinstance(response.data, dict)             and "detail" in response.data and "code" not in response.data:
        response.data["code"] = exc.get_codes()
    return response
