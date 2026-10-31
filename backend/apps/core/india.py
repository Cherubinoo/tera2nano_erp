"""Indian statutory reference data and identifier validation."""
import re

GST_STATES = {
    "01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab", "04": "Chandigarh",
    "05": "Uttarakhand", "06": "Haryana", "07": "Delhi", "08": "Rajasthan", "09": "Uttar Pradesh",
    "10": "Bihar", "11": "Sikkim", "12": "Arunachal Pradesh", "13": "Nagaland", "14": "Manipur",
    "15": "Mizoram", "16": "Tripura", "17": "Meghalaya", "18": "Assam", "19": "West Bengal",
    "20": "Jharkhand", "21": "Odisha", "22": "Chhattisgarh", "23": "Madhya Pradesh", "24": "Gujarat",
    "26": "Dadra and Nagar Haveli and Daman and Diu", "27": "Maharashtra", "29": "Karnataka",
    "30": "Goa", "31": "Lakshadweep", "32": "Kerala", "33": "Tamil Nadu", "34": "Puducherry",
    "35": "Andaman and Nicobar Islands", "36": "Telangana", "37": "Andhra Pradesh", "38": "Ladakh",
    "97": "Other Territory", "99": "Centre Jurisdiction",
}

PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
TAN_RE = re.compile(r"^[A-Z]{4}[0-9]{5}[A-Z]$")
GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
LLPIN_RE = re.compile(r"^[A-Z]{3}-[0-9]{4}$")
IFSC_RE = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
PIN_RE = re.compile(r"^[1-9][0-9]{5}$")
ACCOUNT_RE = re.compile(r"^[0-9]{6,20}$")
HEX_COLOUR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")

_GST_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def gstin_check_char(first14: str) -> str:
    total = 0
    for i, ch in enumerate(first14):
        val = _GST_CHARS.index(ch) * (1 if i % 2 == 0 else 2)
        total += val // 36 + val % 36
    return _GST_CHARS[(36 - total % 36) % 36]


def validate_gstin(value: str, pan: str = ""):
    """Return an error message, or None when the GSTIN is structurally valid."""
    if not GSTIN_RE.match(value):
        return "Enter a valid 15-character GSTIN."
    if value[:2] not in GST_STATES:
        return "GSTIN starts with an unknown state code."
    if gstin_check_char(value[:14]) != value[14]:
        return "GSTIN check character is invalid."
    if pan and value[2:12] != pan:
        return "GSTIN does not contain the company PAN."
    return None
