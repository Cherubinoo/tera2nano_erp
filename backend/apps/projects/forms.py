"""Reading and checking request bodies. Every problem is collected and returned together, keyed by field name."""
import datetime
import uuid
from decimal import Decimal, InvalidOperation

from rest_framework.exceptions import ValidationError

MAX_MONEY = Decimal("100000000000")


class Parser:
    """`partial=True` (PATCH) skips fields that were not sent; otherwise a missing field takes its default."""

    def __init__(self, data, partial=False):
        self.data = data if isinstance(data, dict) or hasattr(data, "get") else {}
        self.partial = partial
        self.errors = {}
        self.out = {}

    def _skip(self, key):
        return self.partial and key not in self.data

    def _fail(self, key, message):
        self.errors[key] = message

    def text(self, key, label, *, required=False, max_len=200, default=""):
        if self._skip(key):
            return
        value = self.data.get(key, default)
        if value is None:
            value = ""
        if not isinstance(value, (str, int, float)) or isinstance(value, bool):
            return self._fail(key, f"{label}: send text.")
        value = str(value).strip()
        if required and not value:
            return self._fail(key, f"Enter the {label.lower()}.")
        if len(value) > max_len:
            return self._fail(key, f"{label} can be at most {max_len} characters.")
        self.out[key] = value

    def date(self, key, label, *, required=False):
        if self._skip(key):
            return
        value = self.data.get(key)
        if value in (None, ""):
            if required:
                return self._fail(key, f"Enter the {label.lower()}.")
            self.out[key] = None
            return
        try:
            self.out[key] = datetime.date.fromisoformat(str(value))
        except ValueError:
            self._fail(key, f"{label}: use a date in YYYY-MM-DD format.")

    def decimal(self, key, label, *, places=2, minimum=Decimal("0"), maximum=MAX_MONEY, default=Decimal("0"), positive=False):
        if self._skip(key):
            return
        value = self.data.get(key)
        if value in (None, ""):
            value = default
        try:
            if isinstance(value, bool):
                raise InvalidOperation
            d = Decimal(str(value).strip())
        except (InvalidOperation, ValueError):
            return self._fail(key, f"{label} must be a number.")
        if not d.is_finite() or d < minimum or (positive and d <= 0):
            return self._fail(key, f"{label} must be more than zero." if positive else f"{label} cannot be negative.")
        if d > maximum:
            return self._fail(key, f"{label} is too large.")
        if d.as_tuple().exponent < -places:
            return self._fail(key, f"{label}: use at most {places} decimal places.")
        self.out[key] = d

    def choice(self, key, label, choices, default=None, required=False):
        if self._skip(key):
            return
        value = self.data.get(key)
        if value in (None, ""):
            if default is None and required:
                return self._fail(key, f"Choose the {label.lower()}.")
            value = default
        if value is not None and value not in choices:
            return self._fail(key, f"{label}: choose one of the listed options.")
        self.out[key] = value

    def uuid(self, key, label):
        if self._skip(key):
            return
        value = self.data.get(key)
        if value in (None, ""):
            self.out[key] = None
            return
        try:
            self.out[key] = uuid.UUID(str(value))
        except ValueError:
            self._fail(key, f"{label}: not a valid id.")

    def tags(self, key="tags"):
        if self._skip(key):
            return
        value = self.data.get(key) or []
        if isinstance(value, str):
            value = [t for t in value.split(",")]
        if not isinstance(value, list) or any(not isinstance(t, str) for t in value):
            return self._fail(key, "Tags: send a list of words.")
        cleaned = []
        for t in value:
            t = t.strip()
            if t and t.lower() not in [c.lower() for c in cleaned]:
                cleaned.append(t)
        if len(cleaned) > 15 or any(len(t) > 30 for t in cleaned):
            return self._fail(key, "Tags: at most 15 tags of up to 30 characters.")
        self.out[key] = cleaned

    def boolean(self, key, label, default=True):
        if self._skip(key):
            return
        value = self.data.get(key, default)
        if not isinstance(value, bool):
            return self._fail(key, f"{label}: send true or false.")
        self.out[key] = value

    def integer(self, key, label, *, minimum=0, maximum=100, default=0):
        if self._skip(key):
            return
        value = self.data.get(key, default)
        if value in (None, ""):
            value = default
        try:
            if isinstance(value, bool):
                raise ValueError
            n = int(str(value))
        except ValueError:
            return self._fail(key, f"{label} must be a whole number.")
        if not minimum <= n <= maximum:
            return self._fail(key, f"{label} must be between {minimum} and {maximum}.")
        self.out[key] = n

    def done(self):
        if self.errors:
            raise ValidationError(self.errors)
        return self.out
