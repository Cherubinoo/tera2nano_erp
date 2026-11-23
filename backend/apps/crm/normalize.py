"""Normalised keys used to spot duplicate leads, contacts and accounts."""
import re


def normal_phone(value):
    """Digits only, and only the last ten, so +91 98765 43210 and 098765-43210 match. Short numbers give no key."""
    digits = re.sub(r"\D", "", value or "")
    return digits[-10:] if len(digits) >= 7 else ""


def normal_name(value):
    """Lower case, punctuation and the usual company suffixes removed, for comparing company names."""
    s = re.sub(r"[^a-z0-9 ]", " ", (value or "").lower())
    s = re.sub(r"\b(pvt|private|ltd|limited|llp|inc|co|corp|corporation|company)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()[:200]
