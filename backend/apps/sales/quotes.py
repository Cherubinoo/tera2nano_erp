"""Quotations that wait before they are issued.

A quotation is normally issued at once (`POST /company/issued-documents/`). When the approvals module has an active rule for
a quotation of that size, the quotation is held here as `pending_approval` instead: nothing is numbered and no PDF is made
until it is approved. Approving issues it exactly as a direct issue would; rejecting sends it back to `draft` with the
approver's note, where it can be edited and sent again.
"""
import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.company.constants import DocType
from apps.company.models import CompanyProfileAuditEvent
from apps.company.services import audit, company as company_service, documents, quotations

from .models import Quotation

Status = Quotation.Status
Action = CompanyProfileAuditEvent.Action


def _audit(q, actor, action, *, new=None, previous=None, reason=""):
    audit.log(q.company, actor, "quotation", q.pk, action, new=new, previous=previous, reason=reason, category="sales")


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def snapshot(q):
    return {"status": q.status, "customer": q.customer_name, "total": str(q.total), "issue_date": str(q.issue_date),
            "number": q.issued_document.reference_number if q.issued_document_id else ""}


def _approvals():
    """The approvals module's API when it is installed; None keeps quotations working on their own."""
    try:
        from apps.approvals import api
        return api if hasattr(api, "submit") else None
    except ImportError:
        return None


def approval_managed():
    """Whether decisions on quotations held for approval must go through the shared approvals workflow."""
    return _approvals() is not None


def worked_out(company, content, issue_date):
    """(grand total, currency) of a quotation as the issue would price it. Raises the same errors the issue would."""
    if not isinstance(content, dict):
        raise ValidationError({"content": "Send the quotation details."})
    resolved = company_service.resolve_for_document(DocType.QUOTATION, issue_date, company)
    record, _printed = quotations.build(company, resolved, content, issue_date)
    return Decimal(record["totals"]["grand_total"]), record["currency"]


def needs_approval(company, total):
    api = _approvals()
    return api is not None and api.requires_approval(company, "quotation", total)


def title_of(q):
    return f"Quotation for {q.customer_name}"


def _submit(q, actor):
    _approvals().submit(q.company, kind="quotation", source_id=str(q.pk), title=title_of(q), amount=q.total, requested_by=actor,
                        summary=f"{q.currency} {q.total}, dated {q.issue_date:%d %b %Y}", url=f"/company/quotations/{q.pk}")


@transaction.atomic
def hold_if_needed(company, actor, *, content, issue_date):
    """Called before a quotation is issued directly. When an approval rule applies, keep it as a pending quotation and
    return it; otherwise return None and let the caller issue it as before."""
    if _approvals() is None:
        return None
    total, currency = worked_out(company, content, issue_date)
    if not needs_approval(company, total):
        return None
    name = str(((content.get("customer") or {}).get("name")) or "").strip()[:200]
    q = Quotation.objects.create(company=company, status=Status.PENDING_APPROVAL, issue_date=issue_date, customer_name=name,
                                 content=content, total=total, currency=currency, submitted_at=_now(),
                                 created_by=actor if getattr(actor, "is_authenticated", False) else None)
    _audit(q, actor, Action.CREATED, new=snapshot(q), reason="Sent for approval")
    _submit(q, actor)
    q.refresh_from_db()
    return q


def _lock(q):
    return Quotation.objects.select_for_update(of=("self",)).select_related("company", "issued_document", "created_by").get(pk=q.pk)


def _issue(q, actor, reason):
    """Number the quotation and make its PDFs (the same call a direct issue makes), then mark it issued."""
    before = snapshot(q)
    origin = q.content.get("source") if isinstance(q.content.get("source"), dict) else {}  # e.g. the CRM deal it started from
    doc = documents.issue_document(DocType.QUOTATION, actor=q.created_by or actor, content={"quotation": q.content},
                                   issue_date=q.issue_date, source_module=origin.get("module") or "sales",
                                   source_id=str(origin.get("id") or q.pk))
    q.issued_document, q.status = doc, Status.ISSUED
    q.save()
    _audit(q, actor, Action.ISSUED, previous=before, new=snapshot(q), reason=reason)


@transaction.atomic
def update_draft(q, actor, *, content, issue_date):
    q = _lock(q)
    if q.status == Status.PENDING_APPROVAL:
        raise ValidationError("This quotation is waiting for approval and cannot be edited. Take it back first.")
    if q.status != Status.DRAFT:
        raise ValidationError("An issued quotation cannot be edited.")
    total, currency = worked_out(q.company, content, issue_date)
    before = snapshot(q)
    q.content, q.issue_date, q.total, q.currency = content, issue_date, total, currency
    q.customer_name = str(((content.get("customer") or {}).get("name")) or "").strip()[:200]
    q.save()
    _audit(q, actor, Action.UPDATED, previous=before, new=snapshot(q))
    return q


@transaction.atomic
def send(q, actor):
    """Issue a draft quotation. Above an approval rule it waits for approval; otherwise it is issued now."""
    q = _lock(q)
    if q.status != Status.DRAFT:
        raise ValidationError("Only a draft quotation can be issued.")
    total, currency = worked_out(q.company, q.content, q.issue_date)
    q.total, q.currency = total, currency
    if not needs_approval(q.company, total):
        _issue(q, actor, "Issued")
        return q
    q.status, q.submitted_at, q.decision_note = Status.PENDING_APPROVAL, _now(), ""
    q.save()
    _audit(q, actor, Action.UPDATED, previous={"status": "draft"}, new={"status": "pending_approval"}, reason="Sent for approval")
    _submit(q, actor)
    q.refresh_from_db()
    return q


@transaction.atomic
def approve(q, actor, note=""):
    """The approval was granted: issue the quotation (number, PDF) as a direct issue would."""
    q = _lock(q)
    if q.status != Status.PENDING_APPROVAL:
        raise ValidationError("Only a quotation that is waiting for approval can be approved.")
    q.decision_note = (note or "").strip()[:300]
    _issue(q, actor, f"Approved{': ' + q.decision_note if q.decision_note else ''}")
    return q


@transaction.atomic
def reject(q, actor, reason):
    q = _lock(q)
    if q.status != Status.PENDING_APPROVAL:
        raise ValidationError("Only a quotation that is waiting for approval can be rejected.")
    if not (reason or "").strip():
        raise ValidationError({"change_reason": "Give the reason for rejecting this quotation."})
    q.status, q.decision_note = Status.DRAFT, f"Rejected: {reason.strip()}"[:300]
    q.save()
    _audit(q, actor, Action.UPDATED, previous={"status": "pending_approval"}, new={"status": "draft"},
           reason=f"Rejected: {reason.strip()}")
    return q


def withdraw(q, actor):
    """The approval request was withdrawn (or the person took the quotation back): it is a draft again."""
    q = _lock(q)
    if q.status == Status.PENDING_APPROVAL:
        q.status = Status.DRAFT
        q.save()
        _audit(q, actor, Action.UPDATED, previous={"status": "pending_approval"}, new={"status": "draft"},
               reason="Approval request withdrawn")
    return q


@transaction.atomic
def take_back(q, actor):
    q = _lock(q)
    if q.status != Status.PENDING_APPROVAL:
        raise ValidationError("Only a quotation that is waiting for approval can be taken back.")
    api = _approvals()
    if api is not None:
        api.cancel_for_source("quotation", q.pk, actor)
    return withdraw(q, actor)


def resubmit(q, actor):
    """A rejected or withdrawn request was sent again from the Approvals module: the draft waits once more."""
    q = _lock(q)
    if q.status == Status.DRAFT:
        q.status, q.submitted_at, q.decision_note = Status.PENDING_APPROVAL, _now(), ""
        q.save()
        _audit(q, actor, Action.UPDATED, previous={"status": "draft"}, new={"status": "pending_approval"},
               reason="Sent for approval again")
    return q


@transaction.atomic
def delete(q, actor):
    q = _lock(q)
    if q.status == Status.PENDING_APPROVAL:
        raise ValidationError("This quotation is waiting for approval. Take it back first, then delete it.")
    if q.status != Status.DRAFT:
        raise ValidationError("An issued quotation cannot be deleted. Void it instead.")
    _audit(q, actor, Action.DELETED, previous=snapshot(q))
    q.delete()


def as_dict(q, *, detail=False):
    out = {"id": str(q.pk), "status": q.status, "status_label": q.get_status_display(), "customer_name": q.customer_name,
           "issue_date": q.issue_date, "total": str(q.total), "currency": q.currency, "decision_note": q.decision_note,
           "submitted_at": q.submitted_at, "created_at": q.created_at,
           "created_by": q.created_by.get_username() if q.created_by_id else "",
           "created_by_id": str(q.created_by_id) if q.created_by_id else None,
           "approval_managed": approval_managed(),
           "issued": ({"id": str(q.issued_document_id), "reference_number": q.issued_document.reference_number}
                      if q.issued_document_id else None)}
    if detail:
        out["content"] = q.content
        out["subject"] = str(q.content.get("subject") or "")
        out["items"] = len(q.content.get("items") or [])
    return out
