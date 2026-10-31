"""CRUD and workflow endpoints for company records."""
import datetime

from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from apps.core.files import inspect_upload

from . import api
from .api import Action, current_company, record_simple, request_reason, save_with_audit
from .constants import AccessClass, DocType, VerificationStatus
from .models import (
    AuthorizedSignatory,
    CompanyAddress,
    CompanyBankAccount,
    CompanyContact,
    CompanyDocument,
    CompanyPartner,
    CompanyPolicy,
    CompanyTaxRegistration,
    FinancialYear,
    IssuedDocument,
    LetterheadTemplate,
)
from .permissions import (
    IsCompanyAdmin,
    IsCompanyAdminOrReadOnly,
    IsFinance,
    IsFinanceOrReadOnly,
    visible_classifications,
)
from .serializers import (
    AuthorizedSignatorySerializer,
    CompanyAddressSerializer,
    CompanyBankAccountSerializer,
    CompanyContactSerializer,
    CompanyDocumentSerializer,
    CompanyPartnerSerializer,
    CompanyPolicySerializer,
    CompanyTaxRegistrationSerializer,
    FinancialYearSerializer,
    IssuedDocumentSerializer,
    IssueDocumentRequestSerializer,
    LetterheadTemplateSerializer,
)
from .services import audit, documents as document_service, letterheads, numbering


class CompanyScopedViewSet(viewsets.ModelViewSet):
    """Base for company-owned records: scoping, audit, deactivate/activate, protected delete."""

    model = None
    entity = ""
    permission_classes = [IsCompanyAdminOrReadOnly]
    # Primary-flag exclusivity: records sharing these field values compete for the single primary.
    primary_scope_fields = ()
    reason_on_deactivate = True

    # -- plumbing -----------------------------------------------------------
    def get_queryset(self):
        qs = self.model.objects.filter(company=current_company())
        active = self.request.query_params.get("active")
        if active in ("true", "false") and hasattr(self.model, "is_active"):
            qs = qs.filter(is_active=(active == "true"))
        return qs

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx["company"] = current_company()
        return ctx

    def paginate_queryset(self, queryset):
        return None  # small reference lists are returned whole

    # -- primary flag ---------------------------------------------------------
    def _demote_other_primaries(self, serializer, company):
        data = serializer.validated_data
        instance = serializer.instance
        is_primary = data.get("is_primary", instance.is_primary if instance else False)
        is_active = data.get("is_active", instance.is_active if instance else True)
        if not (is_primary and is_active):
            return
        scope = {name: data.get(name, getattr(instance, name, None)) for name in self.primary_scope_fields}
        others = self.model.objects.filter(company=company, is_primary=True, is_active=True, **scope)
        if instance is not None:
            others = others.exclude(pk=instance.pk)
        for other in others:
            before = audit.snapshot(other)
            other.is_primary = False
            other.save(update_fields=["is_primary", "updated_at"])
            record_simple(company, self.request, self.entity, other, Action.UPDATED, before=before,
                          reason="Another record was made primary")

    # -- hooks ----------------------------------------------------------------
    def extra_create_fields(self):
        return {}

    @transaction.atomic
    def perform_create(self, serializer):
        company = current_company()
        if hasattr(self.model, "is_primary"):
            self._demote_other_primaries(serializer, company)
        save_with_audit(serializer, entity=self.entity, company=company, request=self.request,
                        company_id=company.id, **self.extra_create_fields())

    @transaction.atomic
    def perform_update(self, serializer):
        company = current_company()
        if hasattr(self.model, "is_primary"):
            self._demote_other_primaries(serializer, company)
        save_with_audit(serializer, entity=self.entity, company=company, request=self.request)

    @transaction.atomic
    def perform_destroy(self, instance):
        company = current_company()
        before = audit.snapshot(instance)
        pk = instance.pk
        instance.delete()  # ProtectedError -> 409 when referenced by documents or other records
        instance.pk = pk
        record_simple(company, self.request, self.entity, instance, Action.DELETED, before=before,
                      reason=request_reason(self.request))

    # -- activate / deactivate ----------------------------------------------
    def set_active(self, instance, active):
        instance.is_active = active
        if not active and hasattr(instance, "is_primary"):
            instance.is_primary = False
        return ["is_active", "is_primary"] if hasattr(instance, "is_primary") else ["is_active"]

    def _toggle(self, request, active):
        reason = request_reason(request)
        if not active and self.reason_on_deactivate and not reason:
            raise ValidationError({"change_reason": "Give a reason for deactivating this record."})
        with transaction.atomic():
            instance = self.get_object()
            company = current_company()
            before = audit.snapshot(instance)
            fields = self.set_active(instance, active)
            if active:
                instance.full_clean()
            instance.save(update_fields=fields + ["updated_at"])
            record_simple(company, request, self.entity, instance,
                          Action.ACTIVATED if active else Action.DEACTIVATED, before=before, reason=reason)
        return Response(self.get_serializer(instance).data)

    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        return self._toggle(request, False)

    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        return self._toggle(request, True)


class CompanyAddressViewSet(CompanyScopedViewSet):
    model = CompanyAddress
    entity = "address"
    serializer_class = CompanyAddressSerializer

    @action(detail=False, methods=["get"], url_path="for-document")
    def for_document(self, request):
        from .services import company as company_service
        doc_type = request.query_params.get("document_type", "")
        if doc_type not in DocType.VALUES:
            raise ValidationError({"document_type": "Unknown document type."})
        address = company_service.resolve_address(current_company(), doc_type)
        if address is None:
            return Response({"detail": "No active address is configured."}, status=status.HTTP_404_NOT_FOUND)
        return Response(self.get_serializer(address).data)


class CompanyPartnerViewSet(CompanyScopedViewSet):
    model = CompanyPartner
    entity = "partner"
    serializer_class = CompanyPartnerSerializer

    def set_active(self, instance, active):
        instance.is_active = active
        return ["is_active"]

    @action(detail=True, methods=["post", "get", "delete"], url_path="signature",
            parser_classes=[MultiPartParser, FormParser], permission_classes=[IsCompanyAdmin])
    def signature(self, request, pk=None):
        return _signature_endpoint(self, request, "partner")


class AuthorizedSignatoryViewSet(CompanyScopedViewSet):
    model = AuthorizedSignatory
    entity = "signatory"
    serializer_class = AuthorizedSignatorySerializer

    def get_queryset(self):
        qs = super().get_queryset()
        if not self.request.user.is_company_admin:
            qs = qs.filter(status=AuthorizedSignatory.Status.ACTIVE)
        return qs

    def set_active(self, instance, active):
        instance.status = AuthorizedSignatory.Status.ACTIVE if active else AuthorizedSignatory.Status.REVOKED
        return ["status"]

    @action(detail=True, methods=["post", "get", "delete"], url_path="signature",
            parser_classes=[MultiPartParser, FormParser], permission_classes=[IsCompanyAdmin])
    def signature(self, request, pk=None):
        return _signature_endpoint(self, request, "signatory")


def _signature_endpoint(view, request, entity):
    instance = view.get_object()
    company = current_company()
    if request.method == "GET":
        if not instance.signature_image:
            return Response({"detail": "No signature stored."}, status=status.HTTP_404_NOT_FOUND)
        audit.record_event(company, request.user, entity, instance.pk, Action.REVEALED,
                           category="signatory", new={"revealed": "signature_image"})
        return api.private_file_response(instance.signature_image)
    with transaction.atomic():
        before = audit.snapshot(instance)
        if request.method == "DELETE":
            if instance.signature_image:
                instance.signature_image.delete(save=False)
            instance.signature_image = None
            instance.signature_checksum = ""
        else:
            upload = inspect_upload(request.FILES.get("file"), images_only=True)
            if instance.signature_image:
                instance.signature_image.delete(save=False)
            instance.signature_image.save("signature", upload.file, save=False)
            instance.signature_checksum = upload.checksum
        instance.save()
        record_simple(company, request, entity, instance, Action.UPDATED, before=before,
                      reason=request_reason(request))
    return Response({"has_signature": bool(instance.signature_image)})


class CompanyContactViewSet(CompanyScopedViewSet):
    model = CompanyContact
    entity = "contact"
    serializer_class = CompanyContactSerializer
    primary_scope_fields = ("contact_type",)
    reason_on_deactivate = False

    @action(detail=False, methods=["get"], url_path="for-document")
    def for_document(self, request):
        from .services import company as company_service
        doc_type = request.query_params.get("document_type", "")
        if doc_type not in DocType.VALUES:
            raise ValidationError({"document_type": "Unknown document type."})
        contact = company_service.resolve_contact(current_company(), doc_type)
        if contact is None:
            return Response({"detail": "No active contact is configured."}, status=status.HTTP_404_NOT_FOUND)
        return Response(self.get_serializer(contact).data)


class CompanyTaxRegistrationViewSet(CompanyScopedViewSet):
    model = CompanyTaxRegistration
    entity = "tax_registration"
    serializer_class = CompanyTaxRegistrationSerializer

    @action(detail=True, methods=["post"], permission_classes=[IsCompanyAdmin])
    def verify(self, request, pk=None):
        decision = request.data.get("decision", "verified")
        if decision not in (VerificationStatus.VERIFIED, VerificationStatus.REJECTED, VerificationStatus.UNVERIFIED):
            raise ValidationError({"decision": "Use 'verified', 'rejected' or 'unverified'."})
        with transaction.atomic():
            instance = self.get_object()
            company = current_company()
            if decision == VerificationStatus.VERIFIED and not instance.supporting_document_id:
                raise ValidationError({"supporting_document": "Attach the supporting document before verifying."})
            before = audit.snapshot(instance)
            instance.verification_status = decision
            instance.verified_by = request.user if decision == VerificationStatus.VERIFIED else None
            instance.verified_at = timezone.now() if decision == VerificationStatus.VERIFIED else None
            instance.save(update_fields=["verification_status", "verified_by", "verified_at", "updated_at"])
            record_simple(company, request, self.entity, instance, Action.VERIFIED, before=before,
                          reason=request_reason(request))
        return Response(self.get_serializer(instance).data)


class CompanyBankAccountViewSet(CompanyScopedViewSet):
    model = CompanyBankAccount
    entity = "bank_account"
    serializer_class = CompanyBankAccountSerializer
    permission_classes = [IsFinanceOrReadOnly]

    def get_queryset(self):
        self._assign_missing_references()
        return super().get_queryset().select_related("company")

    def _assign_missing_references(self):
        """Accounts created before reference codes existed get theirs the first time they are looked at.

        The code carries the date the account was recorded, and accounts are numbered oldest first.
        """
        company = current_company()
        missing = CompanyBankAccount.objects.filter(company=company, reference_code="")
        if not missing.exists():
            return
        with transaction.atomic():
            for account in missing.select_for_update().order_by("created_at"):
                account.reference_code = numbering.allocate_number(
                    company, DocType.BANK_ACCOUNT, actor=self.request.user, on_date=account.created_at.date(),
                ).reference_number
                account.save(update_fields=["reference_code", "updated_at"])
                audit.record_event(company, self.request.user, self.entity, account.pk, Action.UPDATED,
                                   category="bank", changed=["reference_code"],
                                   new={"reference_code": account.reference_code},
                                   reason="Reference code assigned")

    def extra_create_fields(self):
        return {}

    def set_active(self, instance, active):
        instance.is_active = active
        if not active:
            instance.is_primary = False
            instance.show_on_invoices = False
            instance.show_on_quotations = False
        return ["is_active", "is_primary", "show_on_invoices", "show_on_quotations"]

    @action(detail=True, methods=["post"], permission_classes=[IsFinance])
    def reveal(self, request, pk=None):
        """Return the full account number to an authorised user. Each reveal is audited."""
        instance = self.get_object()
        audit.record_event(
            current_company(), request.user, self.entity, instance.pk, Action.REVEALED,
            category="bank", new={"revealed": "account_number"}, reason=request_reason(request),
        )
        return Response({"account_number": instance.reveal_account_number()},
                        headers={"Cache-Control": "no-store"})

    @action(detail=True, methods=["post"], permission_classes=[IsFinance])
    def verify(self, request, pk=None):
        """Second-person verification of payment details.

        The verifier must differ from whoever last changed the details, unless nobody else holds
        a finance or administrator role (then self-verification is allowed and recorded as such).
        """
        from apps.accounts.models import User

        decision = request.data.get("decision", "verified")
        if decision not in (VerificationStatus.VERIFIED, VerificationStatus.REJECTED):
            raise ValidationError({"decision": "Use 'verified' or 'rejected'."})
        with transaction.atomic():
            instance = self.get_object()
            company = current_company()
            note = request_reason(request)
            if decision == VerificationStatus.VERIFIED:
                if not instance.supporting_document_id:
                    raise ValidationError({"supporting_document": "Attach the bank verification document first."})
                if instance.last_changed_by_id == request.user.pk:
                    others = User.objects.filter(is_active=True).exclude(pk=request.user.pk).filter(
                        role__in=["admin", "finance"]
                    ) | User.objects.filter(is_active=True, is_superuser=True).exclude(pk=request.user.pk)
                    if others.exists():
                        raise PermissionDenied(
                            "Payment details must be verified by someone other than the person who entered them."
                        )
                    note = (note + " " if note else "") + "[self-verified: no second approver available]"
            before = audit.snapshot(instance)
            instance.verification_status = decision
            instance.verified_by = request.user if decision == VerificationStatus.VERIFIED else None
            instance.verified_at = timezone.now() if decision == VerificationStatus.VERIFIED else None
            if decision != VerificationStatus.VERIFIED:
                instance.show_on_invoices = instance.show_on_quotations = False
            instance.save(update_fields=["verification_status", "verified_by", "verified_at", "show_on_invoices",
                                         "show_on_quotations", "updated_at"])
            record_simple(company, request, self.entity, instance, Action.VERIFIED, before=before, reason=note)
        return Response(self.get_serializer(instance).data)


class FinancialYearViewSet(CompanyScopedViewSet):
    model = FinancialYear
    entity = "financial_year"
    serializer_class = FinancialYearSerializer

    def get_queryset(self):
        return FinancialYear.objects.filter(company=current_company())

    def _demote_current(self, serializer, company):
        data = serializer.validated_data
        if data.get("is_current"):
            for other in FinancialYear.objects.filter(company=company, is_current=True).exclude(
                pk=getattr(serializer.instance, "pk", None)
            ):
                before = audit.snapshot(other)
                other.is_current = False
                other.save(update_fields=["is_current", "updated_at"])
                record_simple(company, self.request, self.entity, other, Action.UPDATED, before=before,
                              reason="Another year was made current")

    @transaction.atomic
    def perform_create(self, serializer):
        company = current_company()
        self._demote_current(serializer, company)
        save_with_audit(serializer, entity=self.entity, company=company, request=self.request,
                        company_id=company.id)

    @transaction.atomic
    def perform_update(self, serializer):
        company = current_company()
        self._demote_current(serializer, company)
        save_with_audit(serializer, entity=self.entity, company=company, request=self.request)

    def _change(self, request, **fields):
        reason = request_reason(request)
        if not reason:
            raise ValidationError({"change_reason": "Give a reason for this change."})
        with transaction.atomic():
            instance = self.get_object()
            company = current_company()
            before = audit.snapshot(instance)
            for k, v in fields.items():
                setattr(instance, k, v)
            if fields.get("is_current"):
                self._demote_current_instance(instance, company)  # before validation: only one may be current
            instance.full_clean()
            instance.save()
            record_simple(company, request, self.entity, instance, Action.UPDATED, before=before, reason=reason)
        return Response(self.get_serializer(instance).data)

    def _demote_current_instance(self, instance, company):
        for other in FinancialYear.objects.filter(company=company, is_current=True).exclude(pk=instance.pk):
            other.is_current = False
            other.save(update_fields=["is_current", "updated_at"])

    @action(detail=True, methods=["post"], url_path="set-current")
    def set_current(self, request, pk=None):
        return self._change(request, is_current=True)

    @action(detail=True, methods=["post"])
    def lock(self, request, pk=None):
        return self._change(request, is_locked=True)

    @action(detail=True, methods=["post"])
    def unlock(self, request, pk=None):
        return self._change(request, is_locked=False)

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        return self._change(request, status=FinancialYear.Status.CLOSED, is_locked=True, is_current=False)

    def set_active(self, instance, active):  # financial years are closed, not deactivated
        raise ValidationError("Close or lock a financial year instead of deactivating it.")


class CompanyPolicyViewSet(CompanyScopedViewSet):
    model = CompanyPolicy
    entity = "policy"
    serializer_class = CompanyPolicySerializer

    def get_queryset(self):
        return CompanyPolicy.objects.filter(company=current_company())

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        """Create a draft version, optionally copying the currently active one."""
        from .models import CompanyProfile
        company = CompanyProfile.objects.select_for_update().get(pk=current_company().pk)
        payload = {k: v for k, v in request.data.items() if k != "copy_from_current"}
        if str(request.data.get("copy_from_current", "")).lower() in ("1", "true", "yes"):
            active = CompanyPolicy.objects.filter(company=company, status="active").first()
            if active is not None:
                base = CompanyPolicySerializer(active, context=self.get_serializer_context()).data
                for skip in ("id", "version_number", "status", "activated_at", "created_at", "updated_at",
                             "effective_from", "effective_to", "change_summary"):
                    base.pop(skip, None)
                payload = {**base, **payload}
        serializer = self.get_serializer(data=payload)
        serializer.is_valid(raise_exception=True)
        next_version = (CompanyPolicy.objects.filter(company=company).aggregate(m=Max("version_number"))["m"] or 0) + 1
        save_with_audit(serializer, entity=self.entity, company=company, request=request,
                        company_id=company.id, version_number=next_version, status="draft")
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        if self.get_object().status != CompanyPolicy.Status.DRAFT:
            return Response(
                {"detail": "Active and superseded policy versions are fixed so issued documents stay accurate. "
                           "Create a new version instead.", "code": "immutable"},
                status=status.HTTP_409_CONFLICT,
            )
        return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        if self.get_object().status != CompanyPolicy.Status.DRAFT:
            return Response({"detail": "Only draft versions can be deleted.", "code": "immutable"},
                            status=status.HTTP_409_CONFLICT)
        return super().destroy(request, *args, **kwargs)

    @action(detail=False, methods=["get"])
    def current(self, request):
        from .services import company as company_service
        policy = company_service.resolve_policy(current_company())
        if policy is None:
            return Response({"detail": "No policy version is active."}, status=status.HTTP_404_NOT_FOUND)
        return Response(self.get_serializer(policy).data)

    @action(detail=True, methods=["post"], permission_classes=[IsCompanyAdmin])
    def activate(self, request, pk=None):
        from .models import CompanyProfile
        reason = request_reason(request)
        with transaction.atomic():
            company = CompanyProfile.objects.select_for_update().get(pk=current_company().pk)
            policy = self.get_object()
            if policy.status != CompanyPolicy.Status.DRAFT:
                raise ValidationError("Only a draft version can be activated.")
            today = datetime.date.today()
            start = policy.effective_from or today
            policy.full_clean()
            previous = CompanyPolicy.objects.filter(company=company, status="active").first()
            if previous is not None:
                if start <= (previous.effective_from or datetime.date.min):
                    raise ValidationError({"effective_from": "Must be later than the current version's start date."})
                before_prev = audit.snapshot(previous)
                previous.status = CompanyPolicy.Status.SUPERSEDED
                previous.effective_to = start - datetime.timedelta(days=1)
                previous.save(update_fields=["status", "effective_to", "updated_at"])
                record_simple(company, request, self.entity, previous, Action.UPDATED, before=before_prev,
                              reason=f"Superseded by version {policy.version_number}")
            before = audit.snapshot(policy)
            policy.status = CompanyPolicy.Status.ACTIVE
            policy.effective_from = start
            policy.activated_by = request.user
            policy.activated_at = timezone.now()
            policy.save()
            record_simple(company, request, self.entity, policy, Action.ACTIVATED, before=before,
                          reason=reason or policy.change_summary)
        return Response(self.get_serializer(policy).data)

    def set_active(self, instance, active):
        raise ValidationError("Policies are activated or superseded as versions, not deactivated.")


class LetterheadTemplateViewSet(CompanyScopedViewSet):
    """Catalogue of letter templates (LHT-001 ...) and operations (OPS-001 ...).

    Each entry owns its reference-number sequence. A list returns letter templates unless `?kind=operation`
    (or `?kind=all`) is given.
    """

    model = LetterheadTemplate
    entity = "letterhead_template"
    serializer_class = LetterheadTemplateSerializer

    def _kind(self):
        kind = self.request.query_params.get("kind", "letter" if self.action == "list" else "all")
        if kind not in ("letter", "operation", "all"):
            raise ValidationError({"kind": "Use letter, operation or all."})
        return kind

    def get_queryset(self):
        company = current_company()
        kind = self._kind()
        if self.action == "list" and kind != "all" and not LetterheadTemplate.objects.filter(
                company=company, kind=kind).exists():
            letterheads.seed_default_templates(company, kind)  # first visit: offer the standard catalogue
        qs = super().get_queryset().prefetch_related("sequences")
        return qs if kind == "all" else qs.filter(kind=kind)

    @transaction.atomic
    def perform_create(self, serializer):
        super().perform_create(serializer)
        letterheads.ensure_template_sequence(serializer.instance)

    @action(detail=False, methods=["post"], url_path="seed-defaults", permission_classes=[IsCompanyAdmin])
    def seed_defaults(self, request):
        with transaction.atomic():
            company = current_company()
            kind = request.data.get("kind", "letter")
            if kind not in ("letter", "operation"):
                raise ValidationError({"kind": "Use letter or operation."})
            created = letterheads.seed_default_templates(company, kind)
            for t in created:
                audit.record_event(company, request.user, self.entity, t.pk, Action.CREATED,
                                   changed=["code", "name"], new={"code": t.code, "name": t.name},
                                   reason="Default templates created")
        return Response(self.get_serializer(created, many=True).data, status=status.HTTP_201_CREATED)


class CompanyDocumentViewSet(viewsets.ModelViewSet):
    """Company document repository: private files, authenticated downloads, version history."""

    serializer_class = CompanyDocumentSerializer
    permission_classes = [IsFinanceOrReadOnly]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        qs = CompanyDocument.objects.filter(
            company=current_company(), access_classification__in=visible_classifications(self.request.user)
        )
        params = self.request.query_params
        if params.get("document_type"):
            qs = qs.filter(document_type=params["document_type"])
        if params.get("history") != "true":
            qs = qs.filter(is_current=True)
        if params.get("group"):
            qs = qs.filter(document_group=params["group"])
        return qs

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx["company"] = current_company()
        return ctx

    def _check_classification(self, user, value):
        if value == AccessClass.RESTRICTED and not user.is_company_admin:
            raise PermissionDenied("Only administrators can store restricted documents.")

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        company = current_company()
        upload = inspect_upload(request.FILES.get("file"))
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self._check_classification(request.user, serializer.validated_data.get("access_classification"))

        extra = {}
        replaces_id = request.data.get("replaces")
        if replaces_id:
            previous = self.get_queryset().select_for_update().filter(pk=replaces_id, is_current=True).first()
            if previous is None:
                raise ValidationError({"replaces": "The document to replace was not found or is not current."})
            extra = {"document_group": previous.document_group, "version": previous.version + 1}
            previous.is_current = False
            previous.save(update_fields=["is_current", "updated_at"])

        doc = CompanyDocument(
            company=company, original_filename=upload.original_filename, mime_type=upload.mime_type,
            file_size=upload.size, checksum_sha256=upload.checksum, uploaded_by=request.user,
            **serializer.validated_data, **extra,
        )
        doc.file.save(upload.original_filename, upload.file, save=False)
        doc.save()
        audit.record_event(
            company, request.user, "document", doc.pk, Action.CREATED, category="document",
            changed=["document_type", "title", "version", "access_classification", "checksum_sha256"],
            new={"document_type": doc.document_type, "title": doc.title, "version": doc.version,
                 "access_classification": doc.access_classification, "checksum_sha256": doc.checksum_sha256},
        )
        return Response(self.get_serializer(doc).data, status=status.HTTP_201_CREATED)

    @transaction.atomic
    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        new_class = serializer.validated_data.get("access_classification")
        if new_class and new_class != instance.access_classification:
            self._check_classification(request.user, new_class)
            if instance.access_classification == AccessClass.RESTRICTED and not request.user.is_company_admin:
                raise PermissionDenied()
        save_with_audit(serializer, entity="document", company=current_company(), request=request)
        return Response(serializer.data)

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        if not request.user.is_company_admin:
            raise PermissionDenied()
        instance = self.get_object()
        company = current_company()
        before = audit.snapshot(instance)
        path = instance.file
        instance.delete()  # PROTECT: refused while a registration, signatory or profile references it
        path.delete(save=False)
        instance.pk = None
        record_simple(company, request, "document", instance, Action.DELETED, before=before,
                      reason=request_reason(request))
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        doc = self.get_object()
        if doc.access_classification != AccessClass.INTERNAL:
            audit.record_event(current_company(), request.user, "document", doc.pk, Action.DOWNLOADED,
                               category="document", new={"title": doc.title, "version": doc.version})
        return api.private_file_response(doc.file, filename=doc.original_filename, attachment=True,
                                         mime_type=doc.mime_type)

    @action(detail=True, methods=["post"], permission_classes=[IsCompanyAdmin])
    def verify(self, request, pk=None):
        decision = request.data.get("decision", "verified")
        if decision not in ("verified", "rejected", "unverified"):
            raise ValidationError({"decision": "Use 'verified', 'rejected' or 'unverified'."})
        with transaction.atomic():
            doc = self.get_object()
            before = audit.snapshot(doc)
            doc.verification_status = decision
            doc.verified_by = request.user if decision == "verified" else None
            doc.verified_at = timezone.now() if decision == "verified" else None
            doc.save(update_fields=["verification_status", "verified_by", "verified_at", "updated_at"])
            record_simple(current_company(), request, "document", doc, Action.VERIFIED, before=before,
                          reason=request_reason(request))
        return Response(self.get_serializer(doc).data)


class IssuedDocumentViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Generate and retrieve issued documents. Issued documents can never be edited or deleted."""

    serializer_class = IssuedDocumentSerializer

    def get_queryset(self):
        qs = IssuedDocument.objects.filter(company=current_company()).select_related("generated_by")
        params = self.request.query_params
        for key in ("document_type", "status"):
            if params.get(key):
                qs = qs.filter(**{key: params[key]})
        if params.get("source_id"):
            qs = qs.filter(source_id=params["source_id"])
        if params.get("template"):
            qs = qs.filter(letterhead_template__code=params["template"].upper())
        if params.get("q"):
            qs = qs.filter(Q(reference_number__icontains=params["q"]) | Q(title__icontains=params["q"]))
        return qs

    # Documents that move money need a finance role to issue.
    FINANCE_ONLY = {DocType.INVOICE, DocType.CREDIT_NOTE}

    def create(self, request):
        data = IssueDocumentRequestSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        if v["document_type"] in self.FINANCE_ONLY and not request.user.is_finance:
            raise PermissionDenied("Only finance users can issue invoices and credit notes.")
        template = None
        ref = (v.get("letterhead_template") or "").strip()
        if ref:
            scoped = LetterheadTemplate.objects.filter(company=current_company())
            if ref.upper().startswith(("LHT-", "OPS-")):
                template = scoped.filter(code=ref.upper()).first()
            elif len(ref) == 36:
                template = scoped.filter(pk=ref).first()
            if template is None:
                raise ValidationError({"letterhead_template": "Unknown letterhead template or operation."})
        is_operation = v["document_type"] == DocType.OPERATION
        if is_operation and template is None:
            raise ValidationError({"letterhead_template": "Choose the operation this document belongs to."})
        if template is not None and is_operation != (template.kind == LetterheadTemplate.Kind.OPERATION):
            raise ValidationError({"letterhead_template": (
                "Operations documents use an operation ID (OPS-…); letters use a template ID (LHT-…).")})
        document = document_service.issue_document(
            v["document_type"], actor=request.user, content=v["content"], title=v.get("title", ""),
            letterhead_template=template,
            issue_date=v.get("issue_date"), source_module=v.get("source_module", ""),
            source_id=v.get("source_id", ""), numbered=v.get("numbered", True), supersedes=v.get("supersedes"),
        )
        return Response(self.get_serializer(document).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        """`?variant=print` returns the plain version for pre-printed letterhead paper."""
        doc = self.get_object()
        stem = f"{doc.reference_number or doc.id}".replace("/", "-")
        if request.query_params.get("variant") == "print":
            if not doc.print_file:
                return Response({"detail": "This document has no print version."}, status=status.HTTP_404_NOT_FOUND)
            return api.private_file_response(doc.print_file, filename=f"{stem}-print.pdf",
                                             attachment=request.query_params.get("inline") != "true",
                                             mime_type="application/pdf")
        return api.private_file_response(doc.file, filename=f"{stem}.pdf",
                                         attachment=request.query_params.get("inline") != "true",
                                         mime_type="application/pdf")

    @action(detail=True, methods=["get"])
    def snapshot(self, request, pk=None):
        return Response(self.get_object().snapshot)

    @action(detail=True, methods=["post"], permission_classes=[IsCompanyAdmin])
    def void(self, request, pk=None):
        reason = request_reason(request)
        if not reason:
            raise ValidationError({"change_reason": "Give a reason for voiding this document."})
        with transaction.atomic():
            doc = self.get_object()
            if doc.status == IssuedDocument.Status.VOID:
                raise ValidationError("This document is already void.")
            doc.status = IssuedDocument.Status.VOID
            doc.save(update_fields=["status", "updated_at"])
            if doc.reference_id:
                numbering.void_number(doc.reference, reason, request.user)
            audit.record_event(current_company(), request.user, "issued_document", doc.pk, Action.UPDATED,
                               changed=["status"], previous={"status": "issued"}, new={"status": "void"},
                               reason=reason, category="issued_document")
        return Response(self.get_serializer(doc).data)
