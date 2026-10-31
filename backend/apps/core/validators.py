import re

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator

from . import india

pan_validator = RegexValidator(india.PAN_RE, "Enter a valid PAN (e.g. ABCDE1234F).")
tan_validator = RegexValidator(india.TAN_RE, "Enter a valid TAN (e.g. ABCD12345E).")
llpin_validator = RegexValidator(india.LLPIN_RE, "Enter a valid LLPIN (e.g. AAB-1234).")
ifsc_validator = RegexValidator(india.IFSC_RE, "Enter a valid IFSC (e.g. HDFC0001234).")
pin_validator = RegexValidator(india.PIN_RE, "Enter a valid 6-digit PIN code.")
hex_colour_validator = RegexValidator(india.HEX_COLOUR_RE, "Enter a colour as #RRGGBB.")
phone_validator = RegexValidator(r"^\+?[0-9][0-9 \-()]{6,19}$", "Enter a valid telephone number.")


def gstin_validator(value):
    error = india.validate_gstin(value)
    if error:
        raise ValidationError(error)


# --- upload sniffing -------------------------------------------------------

_SIGNATURES = (
    (b"%PDF-", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
)
IMAGE_TYPES = {"image/png", "image/jpeg"}
DOCUMENT_TYPES = IMAGE_TYPES | {"application/pdf"}


def sniff_mime(head: bytes):
    for sig, mime in _SIGNATURES:
        if head.startswith(sig):
            return mime
    return None


def safe_filename(name: str) -> str:
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"[^\w.\- ]", "_", name).strip()
    return name[:200] or "file"
