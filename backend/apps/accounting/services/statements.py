"""Importing bank statements: upload the file, map its columns, and add its transactions to the account's statement lines.

Re-importing an overlapping statement never adds a transaction twice: every line carries a fingerprint (date, amount,
narration, reference and how many identical lines came before it in the same file) that is unique per account.
"""
import datetime
import hashlib
import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.company.models import CompanyProfileAuditEvent

from ..models import Account, BankReconciliation, BankStatement, BankStatementLine
from . import ledger, statement_files as files

Action = CompanyProfileAuditEvent.Action
SAMPLE_ROWS = 15


def check_bank_account(company, account):
    if account is None or account.company_id != company.pk or account.subtype != Account.Subtype.BANK:
        raise ValidationError({"account": "Choose one of the company's bank accounts."})
    if not account.is_active:
        raise ValidationError({"account": "This bank account is switched off."})
    return account


def _read(statement):
    with statement.file.open("rb") as fh:
        return fh.read()


# --------------------------------------------------------------------------- upload and preview

@transaction.atomic
def upload(company, actor, *, account, uploaded):
    """Keep the file privately as a draft statement and return it; nothing is imported until the mapping is confirmed."""
    check_bank_account(company, account)
    if uploaded is None:
        raise ValidationError({"file": "Choose the statement file to upload."})
    if uploaded.size == 0:
        raise ValidationError({"file": "The uploaded file is empty."})
    if uploaded.size > settings.MAX_UPLOAD_BYTES:
        raise ValidationError({"file": f"The file is larger than {settings.MAX_UPLOAD_BYTES // (1024 * 1024)} MB. Download a shorter period."})
    data = uploaded.read()
    fmt = files.detect_format(data[:2048], uploaded.name)
    # Read it once now so a file that cannot be read is refused at upload, not later.
    if fmt == "ofx":
        files.read_ofx(data)
    else:
        files.read_grid(data, fmt)
    from django.core.files.base import ContentFile

    from apps.core.validators import safe_filename
    name = safe_filename(uploaded.name or f"statement.{fmt}")[:255]
    st = BankStatement(company=company, account=account, format=fmt, original_filename=name, file_size=len(data),
                       checksum_sha256=hashlib.sha256(data).hexdigest(),
                       created_by=actor if getattr(actor, "is_authenticated", False) else None)
    st.file.save(name, ContentFile(data), save=False)
    st.save()
    return st


def _saved_mapping(statement, headers):
    """The mapping used for this account's last imported statement, if its columns are still in this file."""
    last = (BankStatement.objects.filter(account=statement.account, status=BankStatement.Status.IMPORTED, format=statement.format)
            .exclude(pk=statement.pk).exclude(mapping={}).order_by("-imported_at").first())
    if last is None:
        return None
    old_headers = [files._norm(h) for h in last.mapping.get("headers", [])]
    new_headers = [files._norm(h) for h in headers]
    out = {k: last.mapping[k] for k in ("date_order", "amount_sign") if k in last.mapping}
    for field in files.FIELDS:
        idx = last.mapping.get(field)
        if idx is None:
            continue
        if old_headers and idx < len(old_headers) and old_headers[idx]:
            name = old_headers[idx]
            if name not in new_headers:
                return None
            out[field] = new_headers.index(name)
        elif idx < len(headers):
            out[field] = idx
    return out if "date" in out else None


def preview(statement, header_row=None):
    """What the browser needs for the mapping step: the headings, sample rows and the guessed (or remembered) mapping."""
    data = _read(statement)
    if statement.format == "ofx":
        rows, balance = files.read_ofx(data)
        return {"needs_mapping": False, "total_rows": len(rows), "headers": ["Date", "Description", "Reference", "Amount"],
                "sample": [[r["date_text"][:8], r["description"], r["reference"], r["amount_text"]] for r in rows[:SAMPLE_ROWS]],
                "mapping": {}, "mapping_source": "ofx", "header_row": 0, "balance": balance}
    grid = files.read_grid(data, statement.format)
    if header_row is None:
        header_row = files.find_header_row(grid)
    header_row = max(0, min(int(header_row), len(grid) - 1))
    headers = grid[header_row] if grid else []
    width = max((len(r) for r in grid[header_row:header_row + 200]), default=0)
    headers = list(headers) + [""] * (width - len(headers))
    saved = _saved_mapping(statement, headers)
    mapping = saved or files.guess_mapping(headers)
    mapping.setdefault("date_order", "dmy")
    mapping.setdefault("amount_sign", "deposit_positive")
    mapping["header_row"] = header_row
    return {"needs_mapping": True, "total_rows": max(len(grid) - header_row - 1, 0), "headers": headers,
            "sample": [list(r) + [""] * (width - len(r)) for r in grid[header_row + 1:header_row + 1 + SAMPLE_ROWS]],
            "top_rows": [list(r)[:12] for r in grid[:min(len(grid), 12)]],
            "mapping": mapping, "mapping_source": "saved" if saved else "guessed"}


# --------------------------------------------------------------------------- import

def _fingerprint(on, amount, description, reference, occurrence):
    key = "|".join([on.isoformat(), f"{amount:.2f}", re.sub(r"\s+", " ", description.upper()).strip(),
                    re.sub(r"\s+", "", reference.upper())])
    return f"{hashlib.sha1(key.encode()).hexdigest()}#{occurrence}"


def _transactions(statement, mapping):
    """(rows read, [(row number, data)], skipped count, problems) from the file under the mapping."""
    data = _read(statement)
    parsed, problems, skipped = [], [], 0
    if statement.format == "ofx":
        rows, balance = files.read_ofx(data)
        for r in rows:
            on = files.ofx_date(r["date_text"])
            try:
                amount, _ = files.parse_amount(r["amount_text"])
            except ValueError:
                amount = None
            if on is None:
                problems.append({"row": r["row"], "reason": f"Could not read the date \"{r['date_text'][:20]}\"."})
            elif not amount:
                problems.append({"row": r["row"], "reason": "There is no amount."})
            else:
                parsed.append((r["row"], {"date": on, "description": r["description"][:500], "reference": r["reference"][:120],
                                          "amount": amount, "balance": None, "fitid": r["fitid"]}))
        if balance and parsed:
            try:
                amt, _ = files.parse_amount(balance["amount"])
            except ValueError:
                amt = None
            last = max(parsed, key=lambda p: p[1]["date"])
            as_of = files.ofx_date(balance["date"])
            if amt is not None and (as_of is None or as_of >= last[1]["date"]):
                last[1]["balance"] = amt
        return len(rows), parsed, 0, problems
    grid = files.read_grid(data, statement.format)
    header_row = mapping.get("header_row", 0)
    header = [files._norm(c) for c in (grid[header_row] if header_row < len(grid) else [])]
    body = grid[header_row + 1:]
    read = 0
    for offset, row in enumerate(body):
        n = header_row + 2 + offset  # 1-based row number in the file
        if not any(str(c or "").strip() for c in row):
            continue
        read += 1
        if [files._norm(c) for c in row] == header:  # the headings repeated on each page
            skipped += 1
            continue
        verdict, out = files.interpret_row(row, mapping)
        if verdict == "ok":
            parsed.append((n, out))
        elif verdict == "skip":
            skipped += 1
        else:
            problems.append({"row": n, "reason": out})
    if read > files.MAX_ROWS:
        raise ValidationError({"file": f"The statement has more than {files.MAX_ROWS} rows. Download a shorter period and import it in parts."})
    return read, parsed, skipped, problems


def import_statement(statement, actor, *, mapping=None, dry_run=False):
    """Read the file under `mapping` and add its transactions. With dry_run, only report what would happen.

    Returns the summary: rows read, imported, skipped as duplicates, skipped (blank / heading / total rows), problems, and
    the first parsed rows for the check screen."""
    with transaction.atomic():
        statement = BankStatement.objects.select_for_update().select_related("account", "company").get(pk=statement.pk)
        if statement.status != BankStatement.Status.DRAFT:
            raise ValidationError("This statement has already been imported.")
        check_bank_account(statement.company, statement.account)
        if statement.format == "ofx":
            clean = {}
        else:
            if not isinstance(mapping, dict):
                raise ValidationError({"mapping": "Send the column mapping."})
            info = preview(statement, header_row=(mapping or {}).get("header_row", 0))
            clean = files.clean_mapping(mapping or {}, len(info["headers"]))
            clean["headers"] = info["headers"]
        read, parsed, skipped, problems = _transactions(statement, clean)
        # Files often run newest first; put them in date order so balances and sequence follow the bank's own order.
        if len(parsed) > 1 and parsed[0][1]["date"] > parsed[-1][1]["date"]:
            parsed.reverse()
        last_rec = BankReconciliation.objects.filter(account=statement.account).order_by("-statement_date").first()
        seen, lines, duplicates = {}, [], 0
        existing = set(BankStatementLine.objects.filter(account=statement.account).values_list("fingerprint", flat=True))
        for seq, (row_no, d) in enumerate(parsed, 1):
            if d.get("fitid"):
                fp = f"ofx:{d['fitid'][:70]}"
            else:
                base = (d["date"], d["amount"], d["description"], d["reference"])
                seen[base] = seen.get(base, 0) + 1
                fp = _fingerprint(*base, seen[base])
            if fp in existing:
                duplicates += 1
                continue
            if last_rec and d["date"] <= last_rec.statement_date:
                problems.append({"row": row_no, "reason": f"Dated on or before {last_rec.statement_date:%d %b %Y}, when this account "
                                                          "was last reconciled. Undo that reconciliation first if this line is missing."})
                continue
            if d["date"] > datetime.date.today() + datetime.timedelta(days=7):
                problems.append({"row": row_no, "reason": f"The date {d['date']:%d %b %Y} is in the future."})
                continue
            existing.add(fp)
            lines.append(BankStatementLine(company=statement.company, account=statement.account, statement=statement, row_number=row_no,
                                           seq=seq, date=d["date"], description=d["description"], reference=d["reference"],
                                           amount=d["amount"], balance=d["balance"], fingerprint=fp))
        problems.sort(key=lambda p: p["row"])
        summary = {"rows_read": read, "imported": len(lines), "duplicates": duplicates, "skipped": skipped, "problems": problems,
                   "period_start": min((l.date for l in lines), default=None), "period_end": max((l.date for l in lines), default=None),
                   "preview": [{"row": l.row_number, "date": l.date, "description": l.description, "reference": l.reference,
                                "amount": l.amount, "balance": l.balance} for l in lines[:20]]}
        if dry_run:
            return summary
        if not lines and not duplicates:
            raise ValidationError({"mapping": "No transactions could be read with these columns. Check the date and amount columns."})
        try:
            with transaction.atomic():
                BankStatementLine.objects.bulk_create(lines)
        except IntegrityError:
            raise ValidationError("Another import of this account was saved at the same moment. Try again.")
        with_balance = [l for l in lines if l.balance is not None]
        statement.status, statement.mapping = BankStatement.Status.IMPORTED, clean
        statement.period_start, statement.period_end = summary["period_start"], summary["period_end"]
        statement.closing_balance = with_balance[-1].balance if with_balance else None
        statement.rows_read, statement.imported_count, statement.duplicate_count = read, len(lines), duplicates
        statement.skipped_count, statement.problems = skipped, problems[:200]
        statement.imported_at = datetime.datetime.now(datetime.timezone.utc)
        statement.save()
        ledger.audit_event(statement.company, actor, "bank_statement", statement.pk, Action.CREATED,
                           new={"account": statement.account.name, "file": statement.original_filename, "rows_read": read,
                                "imported": len(lines), "duplicates": duplicates, "problems": len(problems),
                                "period": f"{summary['period_start'] or ''} to {summary['period_end'] or ''}"},
                           reason="Statement imported")
        return summary


@transaction.atomic
def delete_statement(statement, actor):
    """Throw away a draft, or take back an import whose lines have not been matched, ignored or reconciled yet."""
    statement = BankStatement.objects.select_for_update().select_related("account", "company").get(pk=statement.pk)
    lines = statement.lines.all()
    if lines.exclude(status=BankStatementLine.Status.UNMATCHED).exists() or lines.filter(reconciliation__isnull=False).exists():
        raise ValidationError("Some lines of this statement are already matched or ignored. Undo those first, then remove the import.")
    was = statement.status
    count = lines.count()
    lines.delete()
    if was == BankStatement.Status.IMPORTED:
        ledger.audit_event(statement.company, actor, "bank_statement", statement.pk, Action.DELETED,
                           previous={"account": statement.account.name, "file": statement.original_filename, "imported": count},
                           reason="Import removed")
    statement.file.delete(save=False)
    statement.delete()
