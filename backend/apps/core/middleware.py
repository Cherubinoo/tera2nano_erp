import contextvars
import logging
import uuid

_request_id = contextvars.ContextVar("request_id", default="-")


def get_request_id():
    return _request_id.get()


class RequestIDMiddleware:
    """Attach a correlation ID to every request (honouring a sane inbound X-Request-ID)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        inbound = request.headers.get("X-Request-ID", "")
        rid = inbound if 8 <= len(inbound) <= 64 and inbound.replace("-", "").isalnum() else str(uuid.uuid4())
        token = _request_id.set(rid)
        request.request_id = rid
        try:
            response = self.get_response(request)
        finally:
            _request_id.reset(token)
        response["X-Request-ID"] = rid
        return response


class RequestIDLogFilter(logging.Filter):
    def filter(self, record):
        record.request_id = get_request_id()
        return True
