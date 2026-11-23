"""CRM records: leads, accounts, contacts, deals, activities and the settings lists.

Links to other modules are soft (`customer_id`, `project_id` are plain UUIDs). Accounts stay in the CRM until a deal is
won; only then is a sales Customer created or linked.
"""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q

from apps.company.models import CompanyProfile
from apps.core.models import UUIDModel

from . import constants as c
from .normalize import normal_name, normal_phone

USER = settings.AUTH_USER_MODEL


class CompanyOwned(UUIDModel):
    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="+")

    class Meta:
        abstract = True


# --------------------------------------------------------------------------- settings lists

class PipelineStage(CompanyOwned):
    class Kind(models.TextChoices):
        OPEN = "open", "Open"
        WON = "won", "Won"
        LOST = "lost", "Lost"

    name = models.CharField(max_length=60)
    probability = models.PositiveSmallIntegerField(default=10, validators=[MaxValueValidator(100)])
    position = models.PositiveSmallIntegerField(default=0)
    colour = models.CharField(max_length=7, default="#6366f1")
    kind = models.CharField(max_length=5, choices=Kind.choices, default=Kind.OPEN)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["position", "created_at"]

    def __str__(self):
        return self.name

    @property
    def is_won(self):
        return self.kind == self.Kind.WON

    @property
    def is_lost(self):
        return self.kind == self.Kind.LOST

    def clean(self):
        errors = {}
        self.name = (self.name or "").strip()
        if not self.name:
            errors["name"] = "Enter the stage name."
        if self.kind == self.Kind.WON and self.probability != 100:
            errors["probability"] = "A won stage is always 100%."
        if self.kind == self.Kind.LOST and self.probability != 0:
            errors["probability"] = "A lost stage is always 0%."
        import re
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", self.colour or ""):
            errors["colour"] = "Use a colour like #1F3A5F."
        if self.name and PipelineStage.objects.exclude(pk=self.pk).filter(company_id=self.company_id, name__iexact=self.name).exists():
            errors["name"] = "A stage with this name already exists."
        if errors:
            raise ValidationError(errors)


class LeadSource(CompanyOwned):
    key = models.CharField(max_length=40)
    name = models.CharField(max_length=60)
    position = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["position", "name"]
        constraints = [models.UniqueConstraint(fields=["company", "key"], name="crm_unique_source_key")]

    def __str__(self):
        return self.name

    def clean(self):
        self.name = (self.name or "").strip()
        if not self.name:
            raise ValidationError({"name": "Enter the source name."})
        if LeadSource.objects.exclude(pk=self.pk).filter(company_id=self.company_id, name__iexact=self.name).exists():
            raise ValidationError({"name": "A source with this name already exists."})


class LostReason(CompanyOwned):
    name = models.CharField(max_length=100)
    position = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["position", "name"]

    def __str__(self):
        return self.name

    def clean(self):
        self.name = (self.name or "").strip()
        if not self.name:
            raise ValidationError({"name": "Enter the reason."})
        if LostReason.objects.exclude(pk=self.pk).filter(company_id=self.company_id, name__iexact=self.name).exists():
            raise ValidationError({"name": "This reason already exists."})


# --------------------------------------------------------------------------- people and companies

class Account(CompanyOwned):
    """A company the CRM deals with. `customer_id` is the sales Customer it became once a deal was won."""

    name = models.CharField(max_length=200)
    industry = models.CharField(max_length=100, blank=True)
    website = models.CharField(max_length=200, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    gstin = models.CharField(max_length=15, blank=True)
    address = models.CharField(max_length=300, blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    owner = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    notes = models.TextField(blank=True)
    customer_id = models.UUIDField(null=True, blank=True, db_index=True)
    is_active = models.BooleanField(default=True)
    phone_key = models.CharField(max_length=10, blank=True, db_index=True, editable=False)
    name_key = models.CharField(max_length=200, blank=True, db_index=True, editable=False)

    def save(self, *args, **kwargs):
        self.phone_key, self.name_key = normal_phone(self.phone), normal_name(self.name)
        super().save(*args, **kwargs)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "name"], condition=Q(is_active=True), name="crm_unique_active_account")]

    def __str__(self):
        return self.name

    def clean(self):
        self.name = (self.name or "").strip()
        if not self.name:
            raise ValidationError({"name": "Enter the account name."})
        self.gstin = (self.gstin or "").strip().upper()
        if self.gstin:
            from apps.core import india
            problem = india.validate_gstin(self.gstin)
            if problem:
                raise ValidationError({"gstin": problem})
        if self.is_active and Account.objects.exclude(pk=self.pk).filter(
                company_id=self.company_id, name__iexact=self.name, is_active=True).exists():
            raise ValidationError({"name": "An account with this name already exists."})


class Contact(CompanyOwned):
    name = models.CharField(max_length=150)
    designation = models.CharField(max_length=100, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    account = models.ForeignKey(Account, null=True, blank=True, on_delete=models.SET_NULL, related_name="contacts")
    is_primary = models.BooleanField(default=False)
    owner = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    notes = models.TextField(blank=True)
    phone_key = models.CharField(max_length=10, blank=True, db_index=True, editable=False)

    def save(self, *args, **kwargs):
        self.phone_key = normal_phone(self.phone)
        super().save(*args, **kwargs)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def clean(self):
        self.name = (self.name or "").strip()
        if not self.name:
            raise ValidationError({"name": "Enter the contact's name."})


class Lead(CompanyOwned):
    reference_code = models.CharField(max_length=60, blank=True, default="", editable=False)
    company_name = models.CharField(max_length=200, blank=True)
    contact_name = models.CharField(max_length=150, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    source = models.CharField(max_length=40, default="other")
    industry = models.CharField(max_length=100, blank=True)
    product_interest = models.CharField(max_length=150, blank=True)
    estimated_value = models.DecimalField(max_digits=16, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    status = models.CharField(max_length=12, choices=c.LEAD_STATUSES, default="new")
    rating = models.CharField(max_length=4, choices=c.RATINGS, default="warm")
    owner = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    score = models.PositiveSmallIntegerField(default=0)
    converted_at = models.DateTimeField(null=True, blank=True)
    converted_account = models.ForeignKey(Account, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    converted_contact = models.ForeignKey(Contact, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    converted_deal = models.ForeignKey("Deal", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    phone_key = models.CharField(max_length=10, blank=True, db_index=True, editable=False)
    company_key = models.CharField(max_length=200, blank=True, db_index=True, editable=False)

    def save(self, *args, **kwargs):
        self.phone_key, self.company_key = normal_phone(self.phone), normal_name(self.company_name)
        super().save(*args, **kwargs)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["company", "reference_code"], condition=~Q(reference_code=""),
                                               name="crm_unique_lead_reference")]
        indexes = [models.Index(fields=["status"]), models.Index(fields=["source"]), models.Index(fields=["-created_at"])]

    def __str__(self):
        return self.display_name

    @property
    def display_name(self):
        return self.company_name or self.contact_name or self.reference_code

    def clean(self):
        errors = {}
        self.company_name = (self.company_name or "").strip()
        self.contact_name = (self.contact_name or "").strip()
        if not (self.company_name or self.contact_name):
            errors["company_name"] = "Enter the company name or the contact person."
        if self.phone and sum(ch.isdigit() for ch in self.phone) < 7:
            errors["phone"] = "Enter a phone number with at least 7 digits."
        if errors:
            raise ValidationError(errors)


# --------------------------------------------------------------------------- deals

class Product(CompanyOwned):
    """A lightweight catalogue entry that can be added to a deal as a line."""

    name = models.CharField(max_length=150)
    code = models.CharField(max_length=40, blank=True)
    description = models.CharField(max_length=300, blank=True)
    unit_price = models.DecimalField(max_digits=16, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    gst_rate = models.DecimalField(max_digits=5, decimal_places=2, default=18, validators=[MinValueValidator(0), MaxValueValidator(100)])
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], condition=~Q(code=""), name="crm_unique_product_code")]

    def __str__(self):
        return self.name

    def clean(self):
        self.name = (self.name or "").strip()
        self.code = (self.code or "").strip().upper()
        if not self.name:
            raise ValidationError({"name": "Enter the product or service name."})


class Deal(CompanyOwned):
    reference_code = models.CharField(max_length=60, blank=True, default="", editable=False)
    name = models.CharField(max_length=200)
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="deals")
    contact = models.ForeignKey(Contact, null=True, blank=True, on_delete=models.SET_NULL, related_name="deals")
    stage = models.ForeignKey(PipelineStage, on_delete=models.PROTECT, related_name="deals")
    status = models.CharField(max_length=4, choices=c.DEAL_STATUSES, default="open")
    amount = models.DecimalField(max_digits=16, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    probability = models.PositiveSmallIntegerField(default=0, validators=[MaxValueValidator(100)])
    expected_close_date = models.DateField(null=True, blank=True)
    closed_on = models.DateField(null=True, blank=True)
    owner = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    source = models.CharField(max_length=40, blank=True)
    next_step = models.CharField(max_length=300, blank=True)
    lost_reason = models.CharField(max_length=100, blank=True)
    competitor = models.CharField(max_length=150, blank=True)
    notes = models.TextField(blank=True)
    lead = models.ForeignKey(Lead, null=True, blank=True, on_delete=models.SET_NULL, related_name="deals")
    customer_id = models.UUIDField(null=True, blank=True, db_index=True)
    project_id = models.UUIDField(null=True, blank=True)
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["company", "reference_code"], condition=~Q(reference_code=""),
                                               name="crm_unique_deal_reference")]
        indexes = [models.Index(fields=["status"]), models.Index(fields=["expected_close_date"])]

    def __str__(self):
        return self.name

    def clean(self):
        self.name = (self.name or "").strip()
        if not self.name:
            raise ValidationError({"name": "Enter the deal name."})

    @property
    def weighted(self):
        from decimal import ROUND_HALF_UP, Decimal
        return (self.amount * self.probability / Decimal(100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class DealItem(UUIDModel):
    deal = models.ForeignKey(Deal, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    description = models.CharField(max_length=300)
    quantity = models.DecimalField(max_digits=12, decimal_places=2, default=1)
    unit_price = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    gst_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "created_at"]
        constraints = [models.CheckConstraint(condition=Q(quantity__gt=0), name="crm_item_quantity_positive"),
                       models.CheckConstraint(condition=Q(unit_price__gte=0), name="crm_item_price_not_negative")]


class DealStageHistory(UUIDModel):
    deal = models.ForeignKey(Deal, on_delete=models.CASCADE, related_name="history")
    from_stage = models.ForeignKey(PipelineStage, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    to_stage = models.ForeignKey(PipelineStage, null=True, on_delete=models.SET_NULL, related_name="+")
    from_name = models.CharField(max_length=60, blank=True)
    to_name = models.CharField(max_length=60)
    amount = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    probability = models.PositiveSmallIntegerField(default=0)
    changed_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    note = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["created_at"]


# --------------------------------------------------------------------------- activities

class Activity(CompanyOwned):
    type = models.CharField(max_length=10, choices=c.ACTIVITY_TYPES, default="task")
    subject = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    due_date = models.DateField(null=True, blank=True)
    due_time = models.TimeField(null=True, blank=True)
    priority = models.CharField(max_length=8, choices=c.PRIORITIES, default="normal")
    status = models.CharField(max_length=9, choices=[("open", "Open"), ("completed", "Completed")], default="open")
    completed_at = models.DateTimeField(null=True, blank=True)
    outcome = models.CharField(max_length=500, blank=True)
    owner = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    lead = models.ForeignKey(Lead, null=True, blank=True, on_delete=models.SET_NULL, related_name="activities")
    contact = models.ForeignKey(Contact, null=True, blank=True, on_delete=models.SET_NULL, related_name="activities")
    account = models.ForeignKey(Account, null=True, blank=True, on_delete=models.SET_NULL, related_name="activities")
    deal = models.ForeignKey(Deal, null=True, blank=True, on_delete=models.SET_NULL, related_name="activities")
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["due_date", "due_time", "-created_at"]
        indexes = [models.Index(fields=["status", "due_date"])]

    def __str__(self):
        return self.subject

    def clean(self):
        errors = {}
        self.subject = (self.subject or "").strip()
        if not self.subject:
            errors["subject"] = "Enter a subject."
        if not (self.lead_id or self.contact_id or self.account_id or self.deal_id):
            errors["related_to"] = "Attach the activity to a lead, contact, account or deal."
        if self.due_time and not self.due_date:
            errors["due_date"] = "Enter the due date as well as the time."
        if errors:
            raise ValidationError(errors)
