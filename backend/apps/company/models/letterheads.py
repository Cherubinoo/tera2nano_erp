import re

from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models import UUIDModel

from .profile import CompanyProfile

CODE_RE = re.compile(r"^LHT-\d{3}$")
OPERATION_CODE_RE = re.compile(r"^OPS-\d{3}$")


class LetterheadTemplate(UUIDModel):
    """A catalogue entry that documents are issued on: a letter template (LHT-001 ...) or an operation (OPS-001 ...).

    Documents issued on it get references built from its code.
    """

    class Kind(models.TextChoices):
        LETTER = "letter", "Letter template"
        OPERATION = "operation", "Operation"

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="letterhead_templates")
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.LETTER)
    code = models.CharField(max_length=7, help_text="Template ID, e.g. LHT-001, or operation ID, e.g. OPS-001")
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    # Heading printed on documents issued on this template (can be overridden per document).
    default_title = models.CharField(max_length=120, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "code"]
        constraints = [
            models.UniqueConstraint(fields=["company", "code"], name="unique_letterhead_template_code"),
        ]

    def __str__(self):
        return f"{self.code} {self.name}"

    def clean(self):
        errors = {}
        self.code = (self.code or "").strip().upper()
        operation = self.kind == self.Kind.OPERATION
        if not (OPERATION_CODE_RE if operation else CODE_RE).match(self.code):
            errors["code"] = ("Use the format OPS-001 (OPS, a hyphen and three digits)." if operation
                              else "Use the format LHT-001 (LHT, a hyphen and three digits).")
        elif LetterheadTemplate.objects.exclude(pk=self.pk).filter(company_id=self.company_id, code=self.code).exists():
            errors["code"] = "An entry with this ID already exists."
        if not (self.name or "").strip():
            errors["name"] = "Enter a name."
        if errors:
            raise ValidationError(errors)
