"""Singleton and aggregate endpoints: profile, setup wizard, branding, numbering, tax settings, audit."""
import copy
import datetime

from django.db import transaction
from django.http import HttpResponse
from rest_framework import generics, status
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core import india
from apps.core.files import inspect_upload

from . import api
from .api import Action, current_company, record_simple, request_reason, save_with_audit
from .constants import AccessClass, ContactType, DocType, VerificationStatus
from .models import (
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
    AuthorizedSignatory,
    DocumentNumberSequence,
    FinancialYear,
    IssuedReferenceNumber,
    LetterheadTemplate,
)
from .permissions import IsCompanyAdmin, IsCompanyAdminOrReadOnly
from .serializers import (
    AuditEventSerializer,
    CompanyBrandingSerializer,
    CompanyProfileSerializer,
    CompanyTaxSettingsSerializer,
    DocumentNumberSequenceSerializer,
    GstCalculationSerializer,
    IssuedReferenceNumberSerializer,
)
from .services import audit, company as company_service, documents, numbering, quotations, setup as setup_service, tax


def _serializer_context(request, company):
    return {"request": request, "company": company}


# --------------------------------------------------------------------------- profile

class CompanyProfileView(APIView):
    permission_classes = [IsCompanyAdminOrReadOnly]

    def get(self, request):
        company = current_company()
        return Response(CompanyProfileSerializer(company, context=_serializer_context(request, company)).data)

    @transaction.atomic
    def patch(self, request):
        company = CompanyProfile.objects.select_for_update().get(pk=current_company().pk)
        serializer = CompanyProfileSerializer(
            company, data=request.data, partial=True, context=_serializer_context(request, company)
        )
        serializer.is_valid(raise_exception=True)
        save_with_audit(serializer, entity="profile", company=company, request=request)
        return Response(serializer.data)


class CompanyLogoView(APIView):
    """Company logo. Stored privately; fetched through this authenticated endpoint."""

    permission_classes = [IsCompanyAdminOrReadOnly]
    parser_classes = [MultiPartParser, FormParser]

    def get(self, request):
        company = current_company()
        if not company.logo:
            return Response({"detail": "No logo uploaded."}, status=status.HTTP_404_NOT_FOUND)
        return api.private_file_response(company.logo)

    @transaction.atomic
    def post(self, request):
        company = current_company()
        upload = inspect_upload(request.FILES.get("file"), images_only=True)
        before = audit.snapshot(company)
        if company.logo:
            company.logo.delete(save=False)  # issued documents keep their own logo copy
        company.logo.save("logo", upload.file, save=False)
        company.logo_checksum = upload.checksum
        company.save(update_fields=["logo", "logo_checksum", "updated_at"])
        record_simple(company, request, "profile", company, Action.UPDATED, before=before,
                      reason=request_reason(request))
        return Response({"has_logo": True, "checksum": upload.checksum})

    @transaction.atomic
    def delete(self, request):
        company = current_company()
        before = audit.snapshot(company)
        if company.logo:
            company.logo.delete(save=False)
        company.logo = None
        company.logo_checksum = ""
        company.save(update_fields=["logo", "logo_checksum", "updated_at"])
        record_simple(company, request, "profile", company, Action.UPDATED, before=before,
                      reason=request_reason(request))
        return Response(status=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- setup wizard

class SetupStateView(APIView):
    permission_classes = [IsCompanyAdmin]

    def get(self, request):
        return Response(setup_service.get_state(CompanyProfile.objects.first()))


class SetupStartView(APIView):
    """Step 1. Idempotent: calling it again returns the existing company instead of creating another."""

    permission_classes = [IsCompanyAdmin]

    def post(self, request):
        legal_name = (request.data.get("legal_name") or "").strip()
        display_name = (request.data.get("display_name") or "").strip()
        errors = {}
        if not legal_name:
            errors["legal_name"] = "Enter the legal company name."
        if not display_name:
            errors["display_name"] = "Enter the display name."
        if errors:
            raise ValidationError(errors)
        company, created = setup_service.start_setup(legal_name, display_name, request.user)
        body = setup_service.get_state(company)
        body["created"] = created
        return Response(body, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class SetupProgressView(APIView):
    permission_classes = [IsCompanyAdmin]

    def patch(self, request):
        company = current_company()
        try:
            step = int(request.data.get("step"))
            skipped = [int(s) for s in request.data.get("skipped_steps", company.setup_skipped_steps)]
        except (TypeError, ValueError):
            raise ValidationError({"step": "Step numbers must be integers."})
        setup_service.save_progress(company, step, skipped)
        return Response(setup_service.get_state(company))


class SetupCompleteView(APIView):
    permission_classes = [IsCompanyAdmin]

    def post(self, request):
        company, warnings = setup_service.complete_setup(current_company(), request.user)
        body = setup_service.get_state(company)
        body["warnings"] = warnings
        return Response(body)


# --------------------------------------------------------------------------- singleton settings

class SingletonSettingsView(APIView):
    """GET/PATCH a one-per-company settings record, with audit and reason enforcement."""

    permission_classes = [IsCompanyAdminOrReadOnly]
    model = None
    serializer_class = None
    entity = ""
    bump_version = False

    def instance(self, company):
        obj, _ = self.model.objects.get_or_create(company=company)
        return obj

    def get(self, request):
        company = current_company()
        return Response(self.serializer_class(self.instance(company), context=_serializer_context(request, company)).data)

    @transaction.atomic
    def patch(self, request):
        company = current_company()
        instance = self.instance(company)
        serializer = self.serializer_class(
            instance, data=request.data, partial=True, context=_serializer_context(request, company)
        )
        serializer.is_valid(raise_exception=True)
        if self.bump_version:
            before = audit.snapshot(instance)
            instance = serializer.save()
            changed, previous, new = audit.diff(self.entity, before, audit.snapshot(instance))
            if changed:
                reason = request_reason(request)
                api.require_reason_for(self.entity, changed, before, company, reason)
                instance.version += 1  # newly generated documents record this version; issued ones keep theirs
                instance.save(update_fields=["version", "updated_at"])
                audit.record_event(company, request.user, self.entity, instance.pk, Action.UPDATED,
                                   changed=changed, previous=previous, new=new, reason=reason, instance=instance)
            return Response(self.serializer_class(instance, context=_serializer_context(request, company)).data)
        save_with_audit(serializer, entity=self.entity, company=company, request=request)
        return Response(serializer.data)


class TaxSettingsView(SingletonSettingsView):
    model = CompanyTaxSettings
    serializer_class = CompanyTaxSettingsSerializer
    entity = "tax_settings"


class TaxCalculationView(APIView):
    """Preview a GST split using the company's own state and the supplied place of supply."""

    def post(self, request):
        company = current_company()
        serializer = GstCalculationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        v = serializer.validated_data
        settings_ = company_service.get_tax_settings(company)
        supplier = v.get("supplier_state_code") or tax.supplier_state_code(company)
        place = v.get("place_of_supply_code") or settings_.default_place_of_supply_state_code or supplier
        if not supplier:
            raise ValidationError("Set the registered office state code or GSTIN so the supply can be classified.")
        try:
            result = tax.calculate_gst(
                v["taxable_value"], v["rate"], supplier_state_code=supplier, place_of_supply_code=place,
                treatment=settings_.gst_treatment if settings_.gst_applicable else "not_applicable",
            )
        except ValueError as exc:
            raise ValidationError(str(exc))
        result["supplier_state_code"], result["place_of_supply_code"] = supplier, place
        return Response({k: str(val) if not isinstance(val, (str, bool)) else val for k, val in result.items()})


class QuotationWorkingView(APIView):
    """What the quotation form needs (GET) and the worked-out lines and totals for what is typed so far (POST).

    Nothing is saved and no number is used. The same code prices the quotation when it is issued, so the figures
    shown here are the figures that print.
    """

    def get(self, request):
        from django.conf import settings as django_settings
        company = current_company()
        resolved = company_service.resolve_for_document(DocType.QUOTATION, datetime.date.today(), company)
        ts, policy = resolved.tax_settings, resolved.policy
        gst_on = ts.gst_applicable and ts.gst_treatment == "regular"
        default_rate = quotations._default_rate(ts) if gst_on else None
        return Response({
            "gst_applied": gst_on,
            "gst_rates": [str(r) for r in django_settings.GST_CURRENT_RATES] if gst_on else [],
            "default_gst_rate": quotations.plain_number(default_rate) if default_rate is not None else "",
            "supplier_state_code": tax.supplier_state_code(company) if gst_on else "",
            "default_place_of_supply_state_code": ts.default_place_of_supply_state_code if gst_on else "",
            "show_hsn_sac": ts.show_hsn_sac,
            "validity_days": policy.quotation_validity_days if policy else None,
            "default_terms": policy.quotation_terms if policy else "",
            "payment_terms": policy.payment_terms if policy else "",
            "bank_will_print": resolved.bank_account is not None,
            "currency": company.default_currency,
        })

    def post(self, request):
        company = current_company()
        raw = request.data.get("quotation")
        try:
            issue_date = datetime.date.fromisoformat(request.data.get("issue_date") or "") if request.data.get("issue_date") \
                else datetime.date.today()
        except ValueError:
            raise ValidationError({"issue_date": "Use a date in YYYY-MM-DD format."})
        try:
            quotations.parse(raw)
        except quotations.QuotationError as exc:
            raise ValidationError({"content": str(exc)})
        resolved = company_service.resolve_for_document(DocType.QUOTATION, issue_date, company)
        record, _content = quotations.build(company, resolved, raw, issue_date)
        return Response(record)


# --------------------------------------------------------------------------- branding

class BrandingView(SingletonSettingsView):
    model = CompanyBranding
    serializer_class = CompanyBrandingSerializer
    entity = "branding"
    bump_version = True


class BrandingAssetView(APIView):
    """Logo overrides for the letterhead (primary and alternate)."""

    permission_classes = [IsCompanyAdminOrReadOnly]
    parser_classes = [MultiPartParser, FormParser]
    SLOTS = {"primary_logo": "primary_logo_checksum", "alternate_logo": "alternate_logo_checksum",
             "watermark_image": "watermark_image_checksum"}

    def _slot(self, slot):
        if slot not in self.SLOTS:
            raise ValidationError({"slot": "Unknown asset slot."})
        return slot

    def get(self, request, slot):
        company = current_company()
        branding = company_service.get_branding(company)
        field = getattr(branding, self._slot(slot))  # overrides only; the company logo has its own endpoint
        if not field:
            return Response({"detail": "No image uploaded."}, status=status.HTTP_404_NOT_FOUND)
        return api.private_file_response(field)

    @transaction.atomic
    def post(self, request, slot):
        company = current_company()
        branding = company_service.get_branding(company)
        slot = self._slot(slot)
        upload = inspect_upload(request.FILES.get("file"), images_only=True)
        before = audit.snapshot(branding)
        field = getattr(branding, slot)
        if field:
            field.delete(save=False)
        getattr(branding, slot).save(slot, upload.file, save=False)
        setattr(branding, self.SLOTS[slot], upload.checksum)
        branding.version += 1
        branding.save()
        record_simple(company, request, "branding", branding, Action.UPDATED, before=before)
        return Response({"slot": slot, "uploaded": True})

    @transaction.atomic
    def delete(self, request, slot):
        company = current_company()
        branding = company_service.get_branding(company)
        slot = self._slot(slot)
        before = audit.snapshot(branding)
        field = getattr(branding, slot)
        if field:
            field.delete(save=False)
        setattr(branding, slot, None)
        setattr(branding, self.SLOTS[slot], "")
        branding.version += 1
        branding.save()
        record_simple(company, request, "branding", branding, Action.UPDATED, before=before)
        return Response(status=status.HTTP_204_NO_CONTENT)


class BrandingPreviewView(APIView):
    """Render a sample PDF with unsaved branding values, so the letterhead can be tuned before saving."""

    permission_classes = [IsCompanyAdmin]

    def post(self, request):
        company = current_company()
        saved = company_service.get_branding(company)
        serializer = CompanyBrandingSerializer(
            saved, data=request.data, partial=True, context=_serializer_context(request, company)
        )
        serializer.is_valid(raise_exception=True)  # same validation as saving
        candidate = copy.copy(saved)  # never saved: only used to render the preview
        for key, value in serializer.validated_data.items():
            setattr(candidate, key, value)
        doc_type = request.data.get("document_type", DocType.BUSINESS_LETTER)
        if doc_type not in DocType.PRINTABLE:
            raise ValidationError({"document_type": "Unknown document type."})
        variant = request.data.get("variant", "letterhead")
        if variant not in ("letterhead", "plain"):
            raise ValidationError({"variant": "Use 'letterhead' or 'plain'."})
        pdf_bytes = documents.preview_letterhead(company, candidate, doc_type, variant)
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = 'inline; filename="letterhead-preview.pdf"'
        response["Cache-Control"] = "private, no-store"
        return response


# --------------------------------------------------------------------------- numbering

class NextReferenceView(APIView):
    """Preview of the reference the next document would get. Nothing is allocated; the real number is assigned at issue."""

    def get(self, request):
        doc_type = request.query_params.get("document_type", "")
        if doc_type not in DocType.VALUES:
            raise ValidationError({"document_type": "Unknown document type."})
        raw = request.query_params.get("issue_date")
        try:
            on_date = datetime.date.fromisoformat(raw) if raw else datetime.date.today()
        except ValueError:
            raise ValidationError({"issue_date": "Use a date in YYYY-MM-DD format."})
        return Response({"document_type": doc_type, "issue_date": on_date.isoformat(),
                         "reference_number": numbering.preview_next(current_company(), doc_type, on_date)})


class NumberingCollectionView(APIView):
    """List sequences, create one, or bulk-update several in one audited transaction."""

    permission_classes = [IsCompanyAdminOrReadOnly]

    def get(self, request):
        company = current_company()
        existing = {s.document_type: s for s in DocumentNumberSequence.objects.filter(
            company=company, letterhead_template__isnull=True)}
        ctx = _serializer_context(request, company)
        rows = [DocumentNumberSequenceSerializer(existing[t], context=ctx).data for t, _ in DocType.CHOICES if t in existing]
        missing = [{"value": t, "label": label} for t, label in DocType.CHOICES if t not in existing]
        letter_rows = [
            DocumentNumberSequenceSerializer(s, context=ctx).data
            for s in DocumentNumberSequence.objects.filter(company=company, letterhead_template__isnull=False)
            .select_related("letterhead_template").order_by("letterhead_template__sort_order", "letterhead_template__code")
        ]
        return Response({"results": rows, "unconfigured": missing, "letterhead_sequences": letter_rows})

    @transaction.atomic
    def post(self, request):
        company = current_company()
        serializer = DocumentNumberSequenceSerializer(data=request.data, context=_serializer_context(request, company))
        serializer.is_valid(raise_exception=True)
        save_with_audit(serializer, entity="numbering", company=company, request=request, company_id=company.id)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @transaction.atomic
    def patch(self, request):
        company = current_company()
        items = request.data.get("sequences") if isinstance(request.data, dict) else request.data
        if not isinstance(items, list) or not items:
            raise ValidationError({"sequences": "Send a list of sequence settings, each with a document_type."})
        results, errors = [], {}
        ctx = _serializer_context(request, company)
        for item in items:
            doc_type = item.get("document_type") if isinstance(item, dict) else None
            instance = DocumentNumberSequence.objects.select_for_update().filter(
                company=company, document_type=doc_type, letterhead_template__isnull=True
            ).first()
            serializer = DocumentNumberSequenceSerializer(instance, data=item, partial=instance is not None, context=ctx)
            if not serializer.is_valid():
                errors[doc_type or "?"] = serializer.errors
                continue
            kwargs = {} if instance else {"company_id": company.id}
            results.append(save_with_audit(serializer, entity="numbering", company=company, request=request, **kwargs))
        if errors:
            raise ValidationError(errors)
        return Response({"results": [DocumentNumberSequenceSerializer(r, context=ctx).data for r in results]})


class NumberingDetailView(APIView):
    permission_classes = [IsCompanyAdminOrReadOnly]

    def _get(self, pk):
        company = current_company()
        try:
            return company, DocumentNumberSequence.objects.select_for_update().get(pk=pk, company=company)
        except DocumentNumberSequence.DoesNotExist:
            from rest_framework.exceptions import NotFound
            raise NotFound()

    def get(self, request, pk):
        company, seq = self._get(pk)
        return Response(DocumentNumberSequenceSerializer(seq, context=_serializer_context(request, company)).data)

    @transaction.atomic
    def patch(self, request, pk):
        company, seq = self._get(pk)
        serializer = DocumentNumberSequenceSerializer(
            seq, data=request.data, partial=True, context=_serializer_context(request, company)
        )
        serializer.is_valid(raise_exception=True)
        save_with_audit(serializer, entity="numbering", company=company, request=request)
        return Response(serializer.data)


class NumberingIssuedView(generics.ListAPIView):
    """Recently allocated numbers for one sequence (read-only register)."""

    serializer_class = IssuedReferenceNumberSerializer
    pagination_class = None

    def get_queryset(self):
        return IssuedReferenceNumber.objects.filter(
            company=current_company(), sequence_id=self.kwargs["pk"]
        )[:100]


class NumberingSeedView(APIView):
    permission_classes = [IsCompanyAdmin]

    @transaction.atomic
    def post(self, request):
        company = current_company()
        short_code = (request.data.get("short_code") or company.short_code or "").strip().upper()
        if short_code != company.short_code:
            before = audit.snapshot(company)
            company.short_code = short_code
            company.full_clean()
            company.save(update_fields=["short_code", "updated_at"])
            record_simple(company, request, "profile", company, Action.UPDATED, before=before)
        created = numbering.seed_default_sequences(company, short_code)
        for seq in created:
            audit.record_event(company, request.user, "numbering", seq.pk, Action.CREATED, category="numbering",
                               changed=["document_type", "prefix"],
                               new={"document_type": seq.document_type, "prefix": seq.prefix},
                               reason="Default sequences created")
        ctx = _serializer_context(request, company)
        return Response({"created": [DocumentNumberSequenceSerializer(s, context=ctx).data for s in created]},
                        status=status.HTTP_201_CREATED)


# --------------------------------------------------------------------------- audit history

class AuditPagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 200


class AuditHistoryView(generics.ListAPIView):
    permission_classes = [IsCompanyAdmin]
    serializer_class = AuditEventSerializer
    pagination_class = AuditPagination

    def get_queryset(self):
        qs = CompanyProfileAuditEvent.objects.filter(company=current_company())
        p = self.request.query_params
        for key in ("entity_type", "category", "action", "entity_id", "actor_id"):
            if p.get(key):
                qs = qs.filter(**{key: p[key]})
        if p.get("from"):
            qs = qs.filter(occurred_at__date__gte=p["from"])
        if p.get("to"):
            qs = qs.filter(occurred_at__date__lte=p["to"])
        return qs


# --------------------------------------------------------------------------- overview, lookups, resolve

class OverviewView(APIView):
    """Company overview: identity, completeness checklist, items needing attention."""

    def get(self, request):
        company = current_company()
        today = datetime.date.today()
        soon = today + datetime.timedelta(days=60)
        user = request.user
        profile = CompanyProfileSerializer(company, context=_serializer_context(request, company)).data

        checklist = [
            ("Registered office address", CompanyAddress.objects.filter(company=company, is_active=True,
                                                                        address_type="registered_office").exists()),
            ("PAN recorded", bool(company.pan)),
            ("Company contacts", CompanyContact.objects.filter(company=company, is_active=True).exists()),
            ("Company logo", bool(company.logo)),
            ("Current financial year", FinancialYear.objects.filter(company=company, is_current=True).exists()),
            ("Document numbering", DocumentNumberSequence.objects.filter(company=company, is_active=True).exists()),
            ("Default business terms", CompanyPolicy.objects.filter(company=company, status="active").exists()),
        ]
        docs = CompanyDocument.objects.filter(company=company, is_current=True)
        from .permissions import visible_classifications
        docs = docs.filter(access_classification__in=visible_classifications(user))
        attention = []
        for d in docs.filter(expiry_date__isnull=False, expiry_date__lte=soon).order_by("expiry_date")[:10]:
            label = "expired" if d.expiry_date < today else "expires"
            attention.append({"kind": "document_expiry", "message": f"{d.title} {label} on {d.expiry_date:%d %b %Y}",
                              "id": str(d.id)})
        if user.is_finance:
            for b in CompanyBankAccount.objects.filter(company=company, is_active=True).exclude(
                    verification_status="verified"):
                attention.append({"kind": "bank_unverified", "id": str(b.id),
                                  "message": f"Bank account '{b.display_name}' is not verified"})
            for r in CompanyTaxRegistration.objects.filter(company=company, is_active=True).exclude(
                    verification_status="verified"):
                attention.append({"kind": "registration_unverified", "id": str(r.id),
                                  "message": f"{r.get_registration_type_display()} {r.registration_number} is not verified"})
        fy = FinancialYear.objects.filter(company=company, is_current=True).first()
        policy = company_service.resolve_policy(company)
        return Response({
            "profile": profile,
            "counts": {
                "addresses": CompanyAddress.objects.filter(company=company, is_active=True).count(),
                "partners": CompanyPartner.objects.filter(company=company, is_active=True).count(),
                "contacts": CompanyContact.objects.filter(company=company, is_active=True).count(),
                "documents": docs.count(),
            },
            "current_financial_year": None if fy is None else {"id": str(fy.id), "label": fy.label,
                                                               "start_date": fy.start_date, "end_date": fy.end_date,
                                                               "is_locked": fy.is_locked},
            "active_policy_version": policy.version_number if policy else None,
            "checklist": [{"label": label, "done": done} for label, done in checklist],
            "attention": attention,
        })


class LookupsView(APIView):
    """Choice lists for forms, so the UI never hardcodes them."""

    def get(self, request):
        # The company may not exist yet: the setup wizard needs every choice list before it creates one.
        company = CompanyProfile.objects.first()

        def choices(c):
            return [{"value": v, "label": l} for v, l in c]
        return Response({
            "document_types": choices(DocType.CHOICES),
            "printable_document_types": [t for t in DocType.PRINTABLE],
            "contact_types": choices(ContactType.CHOICES),
            "address_types": choices(CompanyAddress.AddressType.choices),
            "partner_types": choices(CompanyPartner.PartnerType.choices),
            "signatory_authorization_types": choices(AuthorizedSignatory.AuthType.choices),
            "signatory_statuses": choices(AuthorizedSignatory.Status.choices),
            "registration_types": choices(CompanyTaxRegistration.RegType.choices),
            "verification_statuses": choices(VerificationStatus.CHOICES),
            "entity_types": choices(CompanyProfile.EntityType.choices),
            "gst_statuses": choices(CompanyProfile.GstStatus.choices),
            "company_statuses": choices(CompanyProfile.Status.choices),
            "gst_treatments": choices(CompanyTaxSettings.GstTreatment.choices),
            "bank_account_types": choices(CompanyBankAccount.AccountType.choices),
            "document_kinds": choices(CompanyDocument.Kind.choices),
            "letterhead_templates": [
                {"value": t.code, "label": f"{t.code} {t.name}", "id": str(t.id), "is_active": t.is_active,
                 "kind": t.kind, "default_title": t.default_title}
                for t in (LetterheadTemplate.objects.filter(company=company) if company else [])
            ],
            "date_formats": [{"value": "", "label": "No date"}, {"value": "ddmm", "label": "Day and month (DDMM)"},
                             {"value": "ddmmyyyy", "label": "Day, month and year (DDMMYYYY)"}],
            "access_classes": choices(AccessClass.CHOICES),
            "reset_frequencies": choices(DocumentNumberSequence.Reset.choices),
            "alignments": choices(CompanyBranding.Align.choices),
            "page_sizes": choices(CompanyBranding.PageSize.choices),
            "fonts": choices(CompanyBranding.Font.choices),
            "templates": choices(CompanyBranding.Template.choices),
            "financial_year_statuses": choices(FinancialYear.Status.choices),
            "audit_entity_types": [{"value": k, "label": v.label} for k, v in audit.ENTITIES.items()],
            "audit_categories": sorted({v.default_category for v in audit.ENTITIES.values()}
                                       | {c for v in audit.ENTITIES.values() for c in v.categories.values()}),
            "audit_actions": choices(CompanyProfileAuditEvent.Action.choices),
            "gst_states": [{"value": k, "label": f"{k} - {v}"} for k, v in india.GST_STATES.items()],
        })


class ResolveView(APIView):
    """What the company looks like on a given document type: the Company Management service over HTTP."""

    def get(self, request):
        doc_type = request.query_params.get("document_type", "")
        if doc_type not in DocType.VALUES:
            raise ValidationError({"document_type": "Unknown document type."})
        on_date = request.query_params.get("date")
        try:
            on_date = datetime.date.fromisoformat(on_date) if on_date else datetime.date.today()
        except ValueError:
            raise ValidationError({"date": "Use YYYY-MM-DD."})
        resolved = company_service.resolve_for_document(doc_type, on_date, current_company())
        snapshot = company_service.build_snapshot(resolved, doc_type)
        if not request.user.is_finance:
            snapshot["bank"] = None
        return Response(snapshot)
