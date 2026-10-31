from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.core.validators import MaxValueValidator, MinValueValidator

from apps.core.models import UUIDModel
from apps.core.storage import UploadTo, private_storage
from apps.core.validators import hex_colour_validator

from .profile import CompanyProfile


def default_signature_block():
    return {
        "show": True,
        "label": "Authorised Signatory",
        "show_name": True,
        "show_designation": True,
        "show_signature_image": False,
        "space_mm": 18,
    }


class CompanyBranding(UUIDModel):
    class Align(models.TextChoices):
        LEFT = "left", "Left"
        CENTER = "center", "Centre"
        RIGHT = "right", "Right"

    class PageSize(models.TextChoices):
        A4 = "A4", "A4"
        LETTER = "LETTER", "Letter"

    class Font(models.TextChoices):
        HELVETICA = "Helvetica", "Helvetica (sans-serif)"
        TIMES = "Times-Roman", "Times (serif)"
        COURIER = "Courier", "Courier (monospace)"

    class Template(models.TextChoices):
        STANDARD = "standard", "Standard letterhead"
        COMPACT = "compact", "Compact letterhead"
        FOOTER_CONTACT = "footer_contact", "Logo and name on top, address and contacts in footer"

    class WatermarkType(models.TextChoices):
        TEXT = "text", "Text"
        LOGO = "logo", "Company logo"

    company = models.OneToOneField(CompanyProfile, on_delete=models.PROTECT, related_name="branding")
    # Overrides; when empty the company logo from the profile is used.
    primary_logo = models.FileField(upload_to=UploadTo("company/branding"), storage=private_storage, null=True, blank=True)
    primary_logo_checksum = models.CharField(max_length=64, blank=True)
    alternate_logo = models.FileField(upload_to=UploadTo("company/branding"), storage=private_storage, null=True, blank=True)
    alternate_logo_checksum = models.CharField(max_length=64, blank=True)
    # Image used for a logo watermark (often a filled/colour variant of the logo). Falls back to the logo.
    watermark_image = models.FileField(upload_to=UploadTo("company/branding"), storage=private_storage, null=True, blank=True)
    watermark_image_checksum = models.CharField(max_length=64, blank=True)

    logo_position = models.CharField(max_length=6, choices=Align.choices, default=Align.LEFT)
    logo_width_mm = models.PositiveSmallIntegerField(default=35, validators=[MinValueValidator(10), MaxValueValidator(80)])
    header_alignment = models.CharField(max_length=6, choices=Align.choices, default=Align.LEFT)
    footer_alignment = models.CharField(max_length=6, choices=Align.choices, default=Align.CENTER)
    primary_colour = models.CharField(max_length=7, default="#1F3A5F", validators=[hex_colour_validator])
    secondary_colour = models.CharField(max_length=7, default="#5B6B7F", validators=[hex_colour_validator])
    font_family = models.CharField(max_length=20, choices=Font.choices, default=Font.HELVETICA)
    body_font_size = models.PositiveSmallIntegerField(default=10, validators=[MinValueValidator(7), MaxValueValidator(16)])
    heading_font_size = models.PositiveSmallIntegerField(default=14, validators=[MinValueValidator(9), MaxValueValidator(28)])
    footer_font_size = models.PositiveSmallIntegerField(default=8, validators=[MinValueValidator(6), MaxValueValidator(12)])
    margin_top_mm = models.PositiveSmallIntegerField(default=20, validators=[MaxValueValidator(60)])
    margin_bottom_mm = models.PositiveSmallIntegerField(default=20, validators=[MaxValueValidator(60)])
    margin_left_mm = models.PositiveSmallIntegerField(default=20, validators=[MinValueValidator(10), MaxValueValidator(50)])
    margin_right_mm = models.PositiveSmallIntegerField(default=20, validators=[MinValueValidator(10), MaxValueValidator(50)])
    header_height_mm = models.PositiveSmallIntegerField(default=32, validators=[MinValueValidator(10), MaxValueValidator(70)])
    footer_height_mm = models.PositiveSmallIntegerField(default=14, validators=[MinValueValidator(8), MaxValueValidator(40)])
    # Body spacing, so text sits cleanly between the header artwork and the footer of the letterhead.
    line_spacing = models.DecimalField(
        max_digits=3, decimal_places=2, default=Decimal("1.40"),
        validators=[MinValueValidator(Decimal("1.00")), MaxValueValidator(Decimal("2.50"))],
        help_text="Line height as a multiple of the font size.",
    )
    paragraph_spacing_pt = models.PositiveSmallIntegerField(default=6, validators=[MaxValueValidator(40)])
    body_top_gap_mm = models.PositiveSmallIntegerField(
        default=3, validators=[MaxValueValidator(60)], help_text="Space between the header and the first line."
    )
    justify_body = models.BooleanField(default=False)
    show_page_numbers = models.BooleanField(default=True)
    page_number_format = models.CharField(max_length=40, default="Page {page} of {pages}")
    watermark_enabled = models.BooleanField(default=False)
    watermark_type = models.CharField(max_length=4, choices=WatermarkType.choices, default=WatermarkType.TEXT)
    watermark_text = models.CharField(max_length=40, blank=True)
    watermark_size_mm = models.PositiveSmallIntegerField(
        default=90, validators=[MinValueValidator(30), MaxValueValidator(180)],
        help_text="Width of the logo watermark.",
    )
    # Empty means a plain white page.
    page_background_colour = models.CharField(max_length=7, blank=True, validators=[hex_colour_validator])
    phone_label = models.CharField(max_length=12, default="Tel", help_text="Printed before the phone number, e.g. Tel or Mob.")
    watermark_opacity = models.DecimalField(
        max_digits=3, decimal_places=2, default=Decimal("0.08"), validators=[MinValueValidator(Decimal("0.02")), MaxValueValidator(Decimal("0.5"))]
    )
    signature_block = models.JSONField(default=default_signature_block)
    default_page_size = models.CharField(max_length=6, choices=PageSize.choices, default=PageSize.A4)
    default_language = models.CharField(max_length=8, default="en")
    default_template = models.CharField(max_length=20, choices=Template.choices, default=Template.STANDARD)
    show_address_in_header = models.BooleanField(default=True)
    show_contact_in_header = models.BooleanField(default=True)
    footer_text_override = models.CharField(max_length=300, blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        verbose_name_plural = "company branding"

    def clean(self):
        errors = {}
        if self.watermark_enabled and self.watermark_type == self.WatermarkType.TEXT and not self.watermark_text.strip():
            errors["watermark_text"] = "Enter the watermark text or switch the watermark off."
        if self.margin_top_mm + self.margin_bottom_mm + self.header_height_mm + self.footer_height_mm > 160:
            errors["header_height_mm"] = "Margins, header and footer leave too little room for page content."
        block = self.signature_block
        if not isinstance(block, dict):
            errors["signature_block"] = "Signature block must be an object."
        else:
            allowed = set(default_signature_block())
            unknown = set(block) - allowed
            if unknown:
                errors["signature_block"] = f"Unknown signature block settings: {', '.join(sorted(unknown))}."
            elif not isinstance(block.get("space_mm", 0), int) or not 5 <= block.get("space_mm", 18) <= 60:
                errors["signature_block"] = "Signature space must be between 5 and 60 mm."
        if errors:
            raise ValidationError(errors)
