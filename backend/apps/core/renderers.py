"""JSON rendering that never turns money into floating point.

Django REST framework writes a Decimal as a JSON number unless told otherwise, and a float cannot hold 0.1 + 0.2 exactly.
Every Decimal is written as a string instead, with two decimal places for amounts ("1800.00"), which the front end parses
with its own decimal-safe formatting.
"""
from decimal import Decimal

from rest_framework.renderers import JSONRenderer
from rest_framework.utils.encoders import JSONEncoder

CENT = Decimal("0.01")


class DecimalSafeEncoder(JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return format(obj.quantize(CENT), "f") if obj.as_tuple().exponent > -2 else format(obj, "f")
        return super().default(obj)


class DecimalSafeJSONRenderer(JSONRenderer):
    encoder_class = DecimalSafeEncoder
