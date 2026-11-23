import copy
import datetime

from django.conf import settings
from rest_framework import serializers

from apps.core import crypto, india

from .constants import DocType
from .models import (
    AuthorizedSignatory,
    CompanyAddress,
    CompanyBankAccount,
    CompanyBranding,
    CompanyContact,
    CompanyDocument,
    CompanyPartner,
    CompanyPolicy,
    CompanyProfile,
    CompanyProfileAuditEvent,
    CompanyTaxRegistration,
    CompanyTaxSettings,
    DocumentNumberSequence,
    FinancialYear,
    IssuedDocument,
    IssuedReferenceNumber,
    LetterheadTemplate,
)
from .services import numbering, quotations


class BaseSerializer(serializers.ModelSerializer):
    """Runs the model's own `clean()` on the merged candidate so API and admin share one rule set."""

    uppercase_fields = ()
    # Fields the model's clean() normalises (upper-casing, derived state codes); copied back so they are saved.
    sync_after_clean = ()
    read_only_fields_common = ("id", "created_at", "updated_at")

    def get_validators(self):
        # DRF would derive UniqueTogetherValidators from conditional (partial) unique constraints and
        # ignore their conditions. The model's clean() reports friendly errors and the database enforces
        # the constraints, so the derived validators are dropped.
        return []

    def to_internal_value(self, data):
        if self.uppercase_fields and hasattr(data, "copy"):
            data = data.copy()
            for name in self.uppercase_fields:
                value = data.get(name)
                if isinstance(value, str):
                    data[name] = value.strip().upper()
        return super().to_internal_value(data)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        model = self.Meta.model
        names = {f.name for f in model._meta.concrete_fields}
        if self.instance is not None:
            candidate = copy.copy(self.instance)
        elif "company" in names:
            candidate = model(company=self.context["company"])
        else:
            candidate = model()
        for key, value in attrs.items():
            if key in names:
                setattr(candidate, key, value)
        candidate.clean()  # Django ValidationError is converted by DRF
        for key in self.sync_after_clean:
            attrs[key] = getattr(candidate, key)
        return attrs

    def user(self):
        request = self.context.get("request")
        return getattr(request, "user", None)

    def is_admin(self):
        user = self.user()
        return bool(user and user.is_company_admin)

    def is_finance(self):
        user = self.user()
        return bool(user and user.is_finance)


# --------------------------------------------------------------------------- profile

class CompanyProfileSerializer(BaseSerializer):
    has_logo = serializers.SerializerMethodField()
    registered_office = serializers.SerializerMethodField()
    gst_state_name = serializers.SerializerMethodField()

    uppercase_fields = ("pan", "tan", "gstin", "llpin", "short_code", "default_currency")
    sync_after_clean = ("pan", "tan", "gstin", "llpin", "short_code", "default_currency")

    class Meta:
        model = CompanyProfile
        fields = [
            "id", "legal_name", "display_name", "trade_name", "short_code", "has_logo", "description", "industry",
            "business_type", "date_of_incorporation", "website", "primary_email", "primary_phone",
            "alternate_phone", "default_currency", "default_timezone", "status", "legal_entity_type", "llpin",
            "pan", "tan", "gst_registration_status", "gstin", "gst_state_name",
            "incorporation_certificate_reference", "incorporation_certificate", "registered_office",
            "setup_status", "setup_step", "setup_completed_at", "version", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "setup_status", "setup_step", "setup_completed_at", "version",
                            "created_at", "updated_at"]

    def get_has_logo(self, obj):
        return bool(obj.logo)

    def get_gst_state_name(self, obj):
        return india.GST_STATES.get(obj.gstin[:2], "") if obj.gstin else ""

    def get_registered_office(self, obj):
        addr = obj.addresses.filter(address_type="registered_office", is_active=True).first()
        if addr is None:
            return None
        return {"id": str(addr.id), "label": addr.label, "city": addr.city, "state": addr.state,
                "postal_code": addr.postal_code}

    def validate_incorporation_certificate(self, value):
        if value and value.document_type != CompanyDocument.Kind.INCORPORATION:
            raise serializers.ValidationError("Select a document of type 'Incorporation certificate'.")
        return value

    def validate_default_currency(self, value):
        if len(value) != 3 or not value.isalpha():
            raise serializers.ValidationError("Use a 3-letter ISO currency code.")
        return value


# --------------------------------------------------------------------------- addresses

class CompanyAddressSerializer(BaseSerializer):
    class Meta:
        model = CompanyAddress
        fields = [
            "id", "label", "address_type", "line1", "line2", "building_name", "locality", "city", "district",
            "state", "state_code", "postal_code", "country", "is_primary", "is_active", "effective_from",
            "effective_to", "document_types", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


# --------------------------------------------------------------------------- partners & signatories

class CompanyPartnerSerializer(BaseSerializer):
    has_signature = serializers.SerializerMethodField()

    class Meta:
        model = CompanyPartner
        fields = [
            "id", "full_name", "designation", "partner_type", "is_designated_partner", "dpin", "official_email",
            "official_phone", "appointment_date", "cessation_date", "is_active", "authorization_scope",
            "has_signature", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    SENSITIVE = ("dpin", "official_email", "official_phone", "has_signature")

    def validate(self, attrs):
        cessation = attrs.get("cessation_date")
        if cessation and cessation <= datetime.date.today() and "is_active" not in attrs:
            attrs["is_active"] = False  # a partner who has ceased is deactivated automatically
        return super().validate(attrs)

    def get_has_signature(self, obj):
        return bool(obj.signature_image) if self.is_admin() else None

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if not self.is_admin():
            for name in self.SENSITIVE:
                data.pop(name, None)
        return data


class AuthorizedSignatorySerializer(BaseSerializer):
    has_signature = serializers.SerializerMethodField()

    class Meta:
        model = AuthorizedSignatory
        fields = [
            "id", "partner", "name", "designation", "authorization_type", "valid_from", "valid_to",
            "authorized_document_types", "authorization_reference", "supporting_document", "status",
            "has_signature", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_has_signature(self, obj):
        return bool(obj.signature_image) if self.is_admin() else None

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if not self.is_admin():
            data.pop("has_signature", None)
            data.pop("authorization_reference", None)
            data.pop("supporting_document", None)
        return data


class CompanyContactSerializer(BaseSerializer):
    class Meta:
        model = CompanyContact
        fields = [
            "id", "contact_type", "display_name", "email", "phone", "department", "is_primary", "is_active",
            "document_types", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


# --------------------------------------------------------------------------- tax

class CompanyTaxRegistrationSerializer(BaseSerializer):
    class Meta:
        model = CompanyTaxRegistration
        fields = [
            "id", "registration_type", "registration_number", "jurisdiction", "state", "state_code",
            "effective_from", "effective_to", "verification_status", "verified_at", "supporting_document",
            "notes", "is_active", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "verification_status", "verified_at", "created_at", "updated_at"]

    uppercase_fields = ("registration_number",)
    sync_after_clean = ("registration_number", "state", "state_code")


class CompanyTaxSettingsSerializer(BaseSerializer):
    allowed_rates = serializers.SerializerMethodField()

    class Meta:
        model = CompanyTaxSettings
        fields = [
            "id", "gst_applicable", "gst_treatment", "tax_codes", "default_tax_code",
            "default_place_of_supply_state_code", "show_gstin_on_documents", "show_hsn_sac",
            "show_place_of_supply", "show_tax_breakup", "invoice_title", "effective_from", "allowed_rates",
            "updated_at",
        ]
        read_only_fields = ["id", "updated_at"]

    def get_allowed_rates(self, obj):
        return {"current": settings.GST_CURRENT_RATES, "historic": settings.GST_LEGACY_RATES}


class GstCalculationSerializer(serializers.Serializer):
    taxable_value = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=0)
    rate = serializers.DecimalField(max_digits=5, decimal_places=2, min_value=0, max_value=100)
    supplier_state_code = serializers.CharField(required=False, allow_blank=True)
    place_of_supply_code = serializers.CharField(required=False, allow_blank=True)


# --------------------------------------------------------------------------- bank

class CompanyBankAccountSerializer(BaseSerializer):
    account_number = serializers.CharField(write_only=True, required=False, allow_blank=False)
    account_number_masked = serializers.SerializerMethodField()

    uppercase_fields = ("ifsc_code",)
    PRIVILEGED = ("account_holder_name", "ifsc_code", "supporting_document", "verified_at", "effective_from",
                  "effective_to", "show_on_invoices", "show_on_quotations")

    class Meta:
        model = CompanyBankAccount
        fields = [
            "id", "reference_code", "display_name", "bank_name", "branch", "account_holder_name", "account_number",
            "account_number_masked", "ifsc_code", "account_type", "currency", "is_primary", "is_active",
            "effective_from", "effective_to", "verification_status", "verified_at", "supporting_document",
            "show_on_invoices", "show_on_quotations", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "reference_code", "verification_status", "verified_at", "created_at", "updated_at"]

    def get_account_number_masked(self, obj):
        return obj.masked_account_number

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if not self.is_finance():
            for name in self.PRIVILEGED:
                data.pop(name, None)
        return data

    def validate(self, attrs):
        if self.instance is None and "account_number" not in attrs:
            raise serializers.ValidationError({"account_number": "Enter the account number."})
        attrs = super().validate(attrs)
        number = attrs.get("account_number")
        ifsc = attrs.get("ifsc_code") or (self.instance.ifsc_code if self.instance else "")
        if number is not None:
            if not india.ACCOUNT_RE.match(number.strip().replace(" ", "")):
                raise serializers.ValidationError({"account_number": "Account number must be 6 to 20 digits."})
            digest = crypto.keyed_hash(f"{ifsc}:{number.strip().replace(' ', '')}")
            clash = CompanyBankAccount.objects.filter(account_number_hash=digest, is_active=True)
            if self.instance:
                clash = clash.exclude(pk=self.instance.pk)
            if clash.exists():
                raise serializers.ValidationError({"account_number": "This account is already recorded."})
        return attrs


    def create(self, validated_data):
        number = validated_data.pop("account_number")
        instance = CompanyBankAccount(**validated_data)
        instance.ifsc_code = instance.ifsc_code.strip().upper()
        instance.set_account_number(number)
        instance.last_changed_by = self.user()
        instance.reference_code = numbering.allocate_number(
            instance.company, DocType.BANK_ACCOUNT, actor=self.user()).reference_number
        instance.save()
        return instance

    def update(self, instance, validated_data):
        number = validated_data.pop("account_number", None)
        old_hash = instance.account_number_hash
        old_critical = (instance.bank_name, instance.ifsc_code, instance.account_holder_name)
        for key, value in validated_data.items():
            setattr(instance, key, value)
        instance.ifsc_code = instance.ifsc_code.strip().upper()
        if number is not None or instance.ifsc_code != old_critical[1]:
            # The stored hash includes the IFSC, so recompute it from the existing number when only the IFSC changes.
            instance.set_account_number(number if number is not None else instance.reveal_account_number())
        critical_changed = (
            instance.account_number_hash != old_hash
            or (instance.bank_name, instance.ifsc_code, instance.account_holder_name) != old_critical
        )
        if critical_changed:
            instance.last_changed_by = self.user()
            if instance.verification_status == CompanyBankAccount.Verification.VERIFIED:
                # Changed payment details must be re-verified before they can appear on documents again.
                instance.verification_status = CompanyBankAccount.Verification.PENDING
                instance.verified_by = None
                instance.verified_at = None
                instance.show_on_invoices = False
                instance.show_on_quotations = False
        instance.save()
        return instance


# --------------------------------------------------------------------------- branding

class CompanyBrandingSerializer(BaseSerializer):
    has_primary_logo = serializers.SerializerMethodField()
    has_alternate_logo = serializers.SerializerMethodField()
    has_watermark_image = serializers.SerializerMethodField()

    class Meta:
        model = CompanyBranding
        fields = [
            "id", "has_primary_logo", "has_alternate_logo", "has_watermark_image", "logo_position", "logo_width_mm", "header_alignment",
            "footer_alignment", "primary_colour", "secondary_colour", "font_family", "body_font_size",
            "heading_font_size", "footer_font_size", "margin_top_mm", "margin_bottom_mm", "margin_left_mm",
            "margin_right_mm", "header_height_mm", "footer_height_mm", "show_page_numbers", "page_number_format",
            "watermark_enabled", "watermark_type", "watermark_text", "watermark_size_mm", "watermark_opacity",
            "page_background_colour", "phone_label", "line_spacing", "paragraph_spacing_pt", "body_top_gap_mm",
            "justify_body", "signature_block", "default_page_size",
            "default_language", "default_template", "show_address_in_header", "show_contact_in_header",
            "footer_text_override", "version", "updated_at",
        ]
        read_only_fields = ["id", "version", "updated_at"]

    def get_has_primary_logo(self, obj):
        return bool(obj.primary_logo)

    def get_has_alternate_logo(self, obj):
        return bool(obj.alternate_logo)

    def get_has_watermark_image(self, obj):
        return bool(obj.watermark_image)


# --------------------------------------------------------------------------- numbering & FY

class DocumentNumberSequenceSerializer(BaseSerializer):
    next_reference_preview = serializers.SerializerMethodField()
    minimum_next_value = serializers.SerializerMethodField()
    document_type_label = serializers.CharField(source="get_document_type_display", read_only=True)
    letterhead_template_code = serializers.CharField(source="letterhead_template.code", read_only=True, default=None)
    warning = serializers.SerializerMethodField()

    class Meta:
        model = DocumentNumberSequence
        fields = [
            "id", "document_type", "document_type_label", "letterhead_template", "letterhead_template_code",
            "prefix", "include_financial_year", "include_short_year", "date_format", "separator",
            "min_length", "reset_frequency", "next_value", "current_period", "is_active",
            "next_reference_preview", "minimum_next_value", "warning", "updated_at",
        ]
        read_only_fields = ["id", "letterhead_template", "current_period", "updated_at"]

    GST_INVOICE_TYPES = ("invoice", "credit_note")

    def get_warning(self, obj):
        """GST rules cap the serial number on a tax invoice at 16 characters (Rule 46). Advisory, not blocking."""
        if obj.document_type in self.GST_INVOICE_TYPES and obj.letterhead_template_id is None:
            company = obj.company
            if company.gst_registration_status == "registered" and len(obj.preview()) > 16:
                return (f"GST rules limit an invoice number to 16 characters; this one is {len(obj.preview())}. "
                        "Shorten the prefix or the sequence length before issuing GST invoices.")
        return None

    def _fy_label(self):
        cache = self.context.setdefault("_fy_label", {})
        if "label" not in cache:
            fy = FinancialYear.objects.filter(is_current=True).first()
            cache["label"] = fy.label if fy else "YYYY-YY"
        return cache["label"]

    def get_next_reference_preview(self, obj):
        return obj.preview(self._fy_label())

    def get_minimum_next_value(self, obj):
        return numbering.minimum_next_value(obj) if obj.pk else 1

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if self.instance is not None:
            floor = numbering.minimum_next_value(self.instance)
            if "next_value" in attrs and attrs["next_value"] < floor:
                raise serializers.ValidationError(
                    {"next_value": f"Next value cannot be lower than {floor}; that would reuse an issued number."}
                )
            if "document_type" in attrs and attrs["document_type"] != self.instance.document_type:
                raise serializers.ValidationError({"document_type": "The document type of a sequence cannot change."})
        return attrs


class LetterheadTemplateSerializer(BaseSerializer):
    sequence_id = serializers.SerializerMethodField()
    next_reference_preview = serializers.SerializerMethodField()
    issued_count = serializers.SerializerMethodField()

    uppercase_fields = ("code",)
    sync_after_clean = ("code",)

    class Meta:
        model = LetterheadTemplate
        fields = ["id", "kind", "code", "name", "description", "default_title", "sort_order", "is_active",
                  "sequence_id", "next_reference_preview", "issued_count", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]

    def _sequence(self, obj):
        return obj.sequences.first() if obj.pk else None

    def get_sequence_id(self, obj):
        seq = self._sequence(obj)
        return str(seq.id) if seq else None

    def get_next_reference_preview(self, obj):
        seq = self._sequence(obj)
        return seq.preview() if seq else None

    def get_issued_count(self, obj):
        return obj.issued_documents.count() if obj.pk else 0

    def validate(self, attrs):
        if self.instance is not None and "kind" in attrs and attrs["kind"] != self.instance.kind:
            raise serializers.ValidationError({"kind": "A letter template cannot be turned into an operation."})
        if (self.instance is not None and "code" in attrs and attrs["code"] != self.instance.code
                and self.instance.issued_documents.exists()):
            raise serializers.ValidationError(
                {"code": "The ID already used on issued documents cannot change."})
        return super().validate(attrs)


class IssuedReferenceNumberSerializer(serializers.ModelSerializer):
    class Meta:
        model = IssuedReferenceNumber
        fields = ["id", "document_type", "reference_number", "sequence_value", "period", "status", "void_reason",
                  "allocated_at"]


class FinancialYearSerializer(BaseSerializer):
    # Set for a first period that starts at incorporation and is shorter or longer than 12 months.
    irregular_period = serializers.BooleanField(write_only=True, required=False, default=False)

    class Meta:
        model = FinancialYear
        fields = ["id", "label", "start_date", "end_date", "is_current", "status", "is_locked",
                  "irregular_period", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate(self, attrs):
        irregular = attrs.pop("irregular_period", False)
        attrs = super().validate(attrs)
        start = attrs.get("start_date") or (self.instance.start_date if self.instance else None)
        end = attrs.get("end_date") or (self.instance.end_date if self.instance else None)
        if self.instance is not None and self.instance.is_locked and (
            attrs.get("start_date") not in (None, self.instance.start_date)
            or attrs.get("end_date") not in (None, self.instance.end_date)
        ):
            raise serializers.ValidationError("A locked financial year's dates cannot be changed. Unlock it first.")
        if start and end and (end - start).days not in (364, 365):
            # Companies and LLPs in India follow a 12-month year; refuse other lengths unless flagged.
            if not irregular:
                raise serializers.ValidationError(
                    "A financial year normally spans 12 months. Tick 'first period is shorter or longer' if "
                    "this is a first period starting at incorporation."
                )
        return attrs


# --------------------------------------------------------------------------- policies

class CompanyPolicySerializer(BaseSerializer):
    class Meta:
        model = CompanyPolicy
        fields = [
            "id", "version_number", "status", "effective_from", "effective_to", "change_summary", "payment_terms",
            "payment_terms_days", "quotation_validity_days", "invoice_due_days", "quotation_terms", "invoice_terms",
            "project_terms", "confidentiality_notice", "document_footer_text", "refund_cancellation_terms",
            "approval_thresholds", "retention_settings", "email_signature", "correspondence_wording",
            "activated_at", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "version_number", "status", "activated_at", "created_at", "updated_at"]


# --------------------------------------------------------------------------- documents

class CompanyDocumentSerializer(serializers.ModelSerializer):
    uploaded_by_name = serializers.SerializerMethodField()
    is_expired = serializers.SerializerMethodField()
    document_type_label = serializers.CharField(source="get_document_type_display", read_only=True)

    class Meta:
        model = CompanyDocument
        fields = [
            "id", "document_type", "document_type_label", "title", "original_filename", "mime_type", "file_size",
            "checksum_sha256", "uploaded_by_name", "created_at", "access_classification", "expiry_date",
            "is_expired", "verification_status", "verified_at", "partner", "signatory", "tax_registration",
            "bank_account", "address", "document_group", "version", "is_current", "notes",
        ]
        read_only_fields = [
            "id", "original_filename", "mime_type", "file_size", "checksum_sha256", "created_at",
            "verification_status", "verified_at", "document_group", "version", "is_current",
        ]

    def get_uploaded_by_name(self, obj):
        return obj.uploaded_by.get_username() if obj.uploaded_by else ""

    def get_is_expired(self, obj):
        import datetime
        return bool(obj.expiry_date and obj.expiry_date < datetime.date.today())


class IssuedDocumentSerializer(serializers.ModelSerializer):
    generated_by_name = serializers.SerializerMethodField()
    document_type_label = serializers.CharField(source="get_document_type_display", read_only=True)
    letterhead_template_code = serializers.CharField(source="letterhead_template.code", read_only=True, default=None)
    has_print_file = serializers.SerializerMethodField()

    class Meta:
        model = IssuedDocument
        fields = [
            "id", "document_type", "document_type_label", "title", "reference_number", "issue_date",
            "letterhead_template", "letterhead_template_code", "has_print_file",
            "source_module", "source_id", "file_size", "checksum_sha256", "template_key", "template_version",
            "profile_version", "branding_version", "policy_version", "address", "contact", "tax_registration",
            "bank_account", "policy", "signatory", "generated_by_name", "generated_at", "status", "supersedes",
        ]

    def get_generated_by_name(self, obj):
        return obj.generated_by.get_username() if obj.generated_by else ""

    def get_has_print_file(self, obj):
        return bool(obj.print_file)


def _text(value, label, limit, required=False):
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise serializers.ValidationError(f"{label} must be text.")
    value = value.strip()
    if required and not value:
        raise serializers.ValidationError(f"{label} is required.")
    if len(value) > limit:
        raise serializers.ValidationError(f"{label} is too long (maximum {limit} characters).")
    return value


def validate_minutes(m):
    """Validate and normalise the structured content of meeting minutes."""
    if not isinstance(m, dict):
        raise serializers.ValidationError("Minutes must be an object.")
    out = {
        "title": _text(m.get("title"), "Meeting title", 200, required=True),
        "meeting_id": _text(m.get("meeting_id"), "Meeting ID", 60),
        "venue": _text(m.get("venue"), "Venue", 200),
        "minute_taker": _text(m.get("minute_taker"), "Minutes taken by", 200),
        "next_meeting": _text(m.get("next_meeting"), "Next meeting", 300),
    }
    date = _text(m.get("date"), "Meeting date", 10)
    if date:
        try:
            datetime.date.fromisoformat(date)
        except ValueError:
            raise serializers.ValidationError("Meeting date must be YYYY-MM-DD.")
    out["date"] = date
    for key in ("start_time", "end_time"):
        t = _text(m.get(key), key.replace("_", " ").capitalize(), 5)
        if t and not (len(t) == 5 and t[2] == ":" and t[:2].isdigit() and t[3:].isdigit() and int(t[:2]) < 24 and int(t[3:]) < 60):
            raise serializers.ValidationError(f"{key.replace('_', ' ').capitalize()} must be HH:MM.")
        out[key] = t
    if out["start_time"] and out["end_time"] and out["end_time"] <= out["start_time"]:
        raise serializers.ValidationError("The end time must be after the start time.")
    mode = _text(m.get("mode"), "Mode", 12)
    if mode and mode not in ("in_person", "online", "hybrid"):
        raise serializers.ValidationError("Mode must be in_person, online or hybrid.")
    out["mode"] = mode

    def rows(key, label, limit):
        value = m.get(key) or []
        if not isinstance(value, list) or len(value) > limit:
            raise serializers.ValidationError(f"{label}: send a list of at most {limit} entries.")
        return value

    out["attendees"] = []
    for a in rows("attendees", "Attendees", 100):
        if not isinstance(a, dict):
            raise serializers.ValidationError("Each attendee must be an object.")
        status = _text(a.get("status") or "present", "Attendance", 12)
        if status not in ("present", "absent", "apologies"):
            raise serializers.ValidationError("Attendance must be present, absent or apologies.")
        out["attendees"].append({"name": _text(a.get("name"), "Attendee name", 200, required=True),
                                 "designation": _text(a.get("designation"), "Designation", 200), "status": status})
    out["agenda"] = [_text(x, "Agenda item", 500, required=True) for x in rows("agenda", "Agenda", 50)]
    out["items"] = []
    for i in rows("items", "Discussion items", 50):
        if not isinstance(i, dict):
            raise serializers.ValidationError("Each discussion item must be an object.")
        out["items"].append({"heading": _text(i.get("heading"), "Discussion heading", 300, required=True),
                             "discussion": _text(i.get("discussion"), "Discussion", 6000),
                             "decision": _text(i.get("decision"), "Decision", 3000)})
    out["actions"] = []
    for a in rows("actions", "Action items", 100):
        if not isinstance(a, dict):
            raise serializers.ValidationError("Each action item must be an object.")
        due = _text(a.get("due_date"), "Due date", 10)
        if due:
            try:
                datetime.date.fromisoformat(due)
            except ValueError:
                raise serializers.ValidationError("Action due dates must be YYYY-MM-DD.")
        out["actions"].append({"action": _text(a.get("action"), "Action", 1000, required=True),
                               "owner": _text(a.get("owner"), "Owner", 200), "due_date": due})
    if not (out["attendees"] or out["agenda"] or out["items"]):
        raise serializers.ValidationError("Add at least attendees, an agenda or a discussion item.")
    return out


def validate_sections(sections):
    """Headed sections of an operations document: [{"heading": ..., "body": ...}]."""
    if not isinstance(sections, list) or len(sections) > 60:
        raise serializers.ValidationError("Sections: send a list of at most 60 sections.")
    out = []
    for s in sections:
        if not isinstance(s, dict):
            raise serializers.ValidationError("Each section must be an object with a heading and a body.")
        heading = _text(s.get("heading"), "Section heading", 200)
        body = _text(s.get("body"), "Section text", 20000)
        if not heading and not body:
            continue  # blank rows are ignored
        out.append({"heading": heading, "body": body})
    return out


class IssueDocumentRequestSerializer(serializers.Serializer):
    document_type = serializers.ChoiceField(choices=[(v, v) for v in DocType.PRINTABLE])
    title = serializers.CharField(required=False, allow_blank=True, max_length=200)
    issue_date = serializers.DateField(required=False)
    numbered = serializers.BooleanField(required=False, default=True)
    letterhead_template = serializers.CharField(required=False, allow_blank=True, help_text="Template ID, e.g. LHT-002")
    source_module = serializers.CharField(required=False, allow_blank=True, max_length=60)
    source_id = serializers.CharField(required=False, allow_blank=True, max_length=64)
    supersedes = serializers.PrimaryKeyRelatedField(
        queryset=IssuedDocument.objects.all(), required=False, allow_null=True
    )
    content = serializers.DictField()

    def validate(self, attrs):  # noqa: D401
        if "minutes" in attrs["content"] and attrs["document_type"] != DocType.MEETING_MINUTES:
            raise serializers.ValidationError({"content": "Minutes content can only be used for meeting minutes."})
        content = attrs["content"]
        if "billing" in content:
            raise serializers.ValidationError({"content": "Invoices and notes are created in the Sales module."})
        if "quotation" in content:
            if attrs["document_type"] != DocType.QUOTATION:
                raise serializers.ValidationError({"content": "Quotation items can only be used for a quotation."})
            if {"table", "totals", "paragraphs", "recipient", "subject"} & set(content):
                raise serializers.ValidationError(
                    {"content": "A quotation built from items works out its own table, totals and addressee."})
        if attrs["document_type"] == DocType.OPERATION and not (content.get("sections") or content.get("paragraphs")):
            raise serializers.ValidationError({"content": "Paste the content or add at least one section."})
        return attrs

    def validate_content(self, value):
        allowed = {"recipient", "subject", "paragraphs", "meta", "table", "totals", "terms", "include_bank",
                   "include_confidentiality", "minutes", "sections", "quotation"}
        unknown = set(value) - allowed
        if unknown:
            raise serializers.ValidationError(f"Unknown content keys: {', '.join(sorted(unknown))}.")
        if "quotation" in value:
            try:
                quotations.parse(value["quotation"])
            except quotations.QuotationError as exc:
                raise serializers.ValidationError(str(exc))
        if "sections" in value:
            value = {**value, "sections": validate_sections(value["sections"])}
        if "meta" in value:
            meta = value["meta"]
            if not (isinstance(meta, list) and len(meta) <= 30 and all(
                    isinstance(r, list) and len(r) == 2 and all(isinstance(x, str) for x in r) for r in meta)):
                raise serializers.ValidationError("'meta' must be a list of [label, value] pairs.")
        if "minutes" in value:
            value = {**value, "minutes": validate_minutes(value["minutes"])}
        table = value.get("table")
        if table is not None:
            if not isinstance(table, dict) or not isinstance(table.get("columns"), list) \
                    or not isinstance(table.get("rows"), list):
                raise serializers.ValidationError("Table needs 'columns' and 'rows' lists.")
            width = len(table["columns"])
            if any(not isinstance(r, list) or len(r) != width for r in table["rows"]):
                raise serializers.ValidationError("Every table row must have one value per column.")
        for key in ("recipient", "paragraphs"):
            if key in value and not (isinstance(value[key], list) and all(isinstance(v, str) for v in value[key])):
                raise serializers.ValidationError(f"'{key}' must be a list of strings.")
        return value


# --------------------------------------------------------------------------- audit

class AuditEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = CompanyProfileAuditEvent
        fields = [
            "id", "company", "actor_id", "actor_label", "entity_type", "entity_id", "action", "category",
            "changed_fields", "previous_values", "new_values", "reason", "request_id", "profile_version",
            "occurred_at",
        ]
