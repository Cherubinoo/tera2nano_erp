"""Letterhead-aware PDF rendering from a frozen company snapshot.

Everything printed about the company comes from `snapshot` (see company.build_snapshot), never
from live models, so the same snapshot always produces the same letterhead.

Note: the built-in PDF fonts are Latin-1 only, so currency is printed as "INR"/"Rs." and any
character outside Windows-1252 is replaced with "?".
"""
import datetime
import io
import re
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4, letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.platypus import (
    BaseDocTemplate, CondPageBreak, Frame, KeepTogether, PageTemplate, Paragraph, Spacer, Table, TableStyle,
)

from . import richtext

TEMPLATE_VERSION = "1.0"
PAGE_SIZES = {"A4": A4, "LETTER": letter}
BOLD = {"Helvetica": "Helvetica-Bold", "Times-Roman": "Times-Bold", "Courier": "Courier-Bold"}
ALIGN = {"left": TA_LEFT, "center": TA_CENTER, "right": TA_RIGHT}


def _clean(text):
    return richtext.clean(text)


def _para(text):
    return escape(_clean(text)).replace("\n", "<br/>")


def _x_for(align, left, right, width):
    if align == "center":
        return (left + right) / 2 - width / 2
    if align == "right":
        return right - width
    return left


def _header_lines(snap):
    company, b = snap["company"], snap["branding"]
    lines = []
    address = snap.get("address")
    if address and b["show_address_in_header"]:
        lines.extend(address["lines"])
    contact_bits = []
    if b["show_contact_in_header"]:
        contact = snap.get("contact")
        phone = (contact or {}).get("phone") or company.get("primary_phone")
        email = (contact or {}).get("email") or company.get("primary_email")
        label = (b.get("phone_label") or "Tel").strip()
        contact_bits = [p for p in (f"{label}: {phone}" if phone else "", email, company.get("website")) if p]
    if contact_bits:
        lines.append("  |  ".join(contact_bits))
    ids = []
    tax = snap["tax"]
    if tax.get("gstin") and tax.get("show_gstin"):
        ids.append(f"GSTIN: {tax['gstin']}")
    if company.get("pan"):
        ids.append(f"PAN: {company['pan']}")
    if company.get("llpin"):
        ids.append(f"LLPIN: {company['llpin']}")
    if ids:
        lines.append("  |  ".join(ids))
    return lines


def _contact_line(snap):
    """'Mob: +91 ...  Email: ...  Web: ...' using the selected contact, else the company profile."""
    company, b = snap["company"], snap["branding"]
    contact = snap.get("contact") or {}
    phone = contact.get("phone") or company.get("primary_phone")
    email = contact.get("email") or company.get("primary_email")
    web = (company.get("website") or "").replace("https://", "").replace("http://", "").rstrip("/")
    label = (b.get("phone_label") or "Tel").strip()
    bits = [f"{label}: {phone}" if phone else "", f"Email: {email}" if email else "", f"Web: {web}" if web else ""]
    return "  ".join(bit for bit in bits if bit)


def _footer_text(snap):
    b = snap["branding"]
    if b.get("footer_text_override"):
        return b["footer_text_override"]
    policy = snap.get("policy") or {}
    return policy.get("footer_text", "")


def _decorator_canvas(snap, logo_bytes, draft, watermark_bytes=None, plain=False):
    """`plain` leaves the letterhead out (no header, footer, watermark or background) so the page can be
    printed on pre-printed official letterhead paper; margins and text positions stay identical."""
    b = snap["branding"]
    page_w, page_h = PAGE_SIZES[b["page_size"]]
    top, right, bottom, left = (m * mm for m in b["margins_mm"])
    primary = colors.HexColor(b["primary_colour"])
    secondary = colors.HexColor(b["secondary_colour"])
    font, bold = b["font_family"], BOLD[b["font_family"]]
    logo = ImageReader(io.BytesIO(logo_bytes)) if logo_bytes else None
    watermark = ImageReader(io.BytesIO(watermark_bytes)) if watermark_bytes else logo

    class DecoratedCanvas(pdfcanvas.Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._pages = []

        def showPage(self):
            self._pages.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._pages)
            for state in self._pages:
                self.__dict__.update(state)
                self._decorate(total)
                super().showPage()
            super().save()

        # -- decoration -----------------------------------------------------
        def underlay(self):
            """Page background and watermark. Called before any content so text stays on top."""
            if plain:
                return
            colour = b.get("page_background_colour")
            if colour:
                self.saveState()
                self.setFillColor(colors.HexColor(colour))
                self.rect(0, 0, page_w, page_h, stroke=0, fill=1)
                self.restoreState()
            if b.get("watermark_enabled") and b.get("watermark_type") == "logo" and watermark is not None:
                self._logo_watermark()
            elif b.get("watermark"):
                self._watermark()

        def _logo_watermark(self):
            iw, ih = watermark.getSize()
            w = b.get("watermark_size_mm", 90) * mm
            h = w * ih / iw
            self.saveState()
            self.setFillAlpha(float(b["watermark_opacity"]))
            self.drawImage(watermark, (page_w - w) / 2, (page_h - h) / 2, w, h, mask="auto")
            self.restoreState()

        def _decorate(self, total):
            self.saveState()
            if draft:
                self._draft_mark()
            if plain:
                self._plain_page_number(total)
            else:
                self._header()
                self._footer(total)
            self.restoreState()

        def _plain_page_number(self, total):
            """The only mark on a plain page: a small page number where the letterhead footer leaves room."""
            if not b["show_page_numbers"]:
                return
            size = b["footer_font_size"]
            label = _clean(b["page_number_format"]).replace("{page}", str(self._pageNumber)).replace(
                "{pages}", str(total))
            self.setFillColor(secondary)
            self.setFont(font, size)
            self.drawRightString(page_w - right, bottom + 2 * (size + 4) + 6, label)

        def _watermark(self):
            self.saveState()
            self.setFillColor(secondary)
            self.setFillAlpha(float(b["watermark_opacity"]))
            self.setFont(bold, 64)
            self.translate(page_w / 2, page_h / 2)
            self.rotate(45)
            self.drawCentredString(0, 0, _clean(b["watermark"]))
            self.restoreState()

        def _draft_mark(self):
            self.saveState()
            self.setFillColor(colors.HexColor("#B42318"))
            self.setFillAlpha(0.10)
            self.setFont("Helvetica-Bold", 90)
            self.translate(page_w / 2, page_h / 2)
            self.rotate(35)
            self.drawCentredString(0, 0, "PREVIEW")
            self.restoreState()

        def _header(self):
            if b["template"] == "footer_contact":
                return self._header_inline()
            company = snap["company"]
            area_left, area_right = left, page_w - right
            y_top = page_h - top
            header_h = b["header_height_mm"] * mm
            compact = b["template"] == "compact"
            gap = 4 * mm

            logo_w = logo_h = 0
            if logo is not None:
                iw, ih = logo.getSize()
                logo_w = b["logo_width_mm"] * mm * (0.6 if compact else 1)
                logo_h = logo_w * ih / iw
                max_h = header_h * (0.8 if b["logo_position"] == "center" and not compact else 0.9)
                if logo_h > max_h:
                    logo_w, logo_h = logo_w * max_h / logo_h, max_h

            text_left, text_right = area_left, area_right
            logo_x = logo_y = None
            if logo is not None:
                if b["logo_position"] == "center" and not compact:
                    logo_x, logo_y = _x_for("center", area_left, area_right, logo_w), y_top - logo_h
                    text_top = y_top - logo_h - 2 * mm
                else:
                    on_left = b["logo_position"] != "right"
                    logo_x = area_left if on_left else area_right - logo_w
                    logo_y = y_top - logo_h
                    if on_left:
                        text_left = area_left + logo_w + gap
                    else:
                        text_right = area_right - logo_w - gap
                    text_top = y_top
            else:
                text_top = y_top

            if logo is not None:
                self.drawImage(logo, logo_x, logo_y, logo_w, logo_h, mask="auto")

            name_size = b["heading_font_size"] * (0.85 if compact else 1)
            self.setFillColor(primary)
            self.setFont(bold, name_size)
            y = text_top - name_size
            name = _clean(company["display_name"])
            self.drawString(_x_for(b["header_alignment"], text_left, text_right,
                                   self.stringWidth(name, bold, name_size)), y, name)

            small = max(b["body_font_size"] - 2, 6.5)
            self.setFillColor(secondary)
            self.setFont(font, small)
            if not compact:
                for line in _header_lines(snap):
                    y -= small + 2
                    if y < y_top - header_h + 2:
                        break
                    line = _clean(line)
                    self.drawString(_x_for(b["header_alignment"], text_left, text_right,
                                           self.stringWidth(line, font, small)), y, line)
            else:
                ids = [ln for ln in _header_lines(snap) if "GSTIN" in ln or "PAN" in ln][:1]
                for line in ids:
                    y -= small + 2
                    line = _clean(line)
                    self.drawString(_x_for(b["header_alignment"], text_left, text_right,
                                           self.stringWidth(line, font, small)), y, line)

            self.setStrokeColor(primary)
            self.setLineWidth(1)
            self.line(area_left, y_top - header_h, area_right, y_top - header_h)

        def _header_inline(self):
            """Logo on the left with the legal name beside it, vertically centred. Nothing else."""
            area_left, area_right = left, page_w - right
            y_top = page_h - top
            gap = 5 * mm
            logo_w = logo_h = 0
            if logo is not None:
                iw, ih = logo.getSize()
                logo_w = b["logo_width_mm"] * mm
                logo_h = logo_w * ih / iw
                max_h = b["header_height_mm"] * mm
                if logo_h > max_h:
                    logo_w, logo_h = logo_w * max_h / logo_h, max_h
                self.drawImage(logo, area_left, y_top - logo_h, logo_w, logo_h, mask="auto")
            name = _clean(snap["company"]["legal_name"]).upper()
            size = min(b["heading_font_size"] * 1.5, 30)
            x = area_left + (logo_w + gap if logo is not None else 0)
            room = area_right - x
            while size > 9 and self.stringWidth(name, bold, size) > room:
                size -= 0.5
            baseline = y_top - (logo_h / 2 if logo is not None else size) - size * 0.35
            self.setFillColor(primary)
            self.setFont(bold, size)
            self.drawString(x, baseline, name)

        def _footer_inline(self, total):
            """Address and contacts centred in the footer, no rule (matches the printed letterhead)."""
            area_left, area_right = left, page_w - right
            size = b["footer_font_size"]
            lines = []
            address = snap.get("address")
            if address:
                lines.extend(_wrap(_clean(address.get("single_line") or ", ".join(address["lines"])), self, font, size,
                                   area_right - area_left))
            contact = _contact_line(snap)
            if contact:
                lines.append(_clean(contact))
            self.setFillColor(primary)
            y = bottom + size * 1.2 + (len(lines) - 1) * (size + 4)
            for line in lines:
                tx = self.beginText()
                tx.setFont(font, size)
                tx.setCharSpace(0.4)
                width = self.stringWidth(line, font, size) + 0.4 * len(line)
                tx.setTextOrigin(_x_for("center", area_left, area_right, width), y)
                tx.textOut(line)
                self.drawText(tx)
                y -= size + 4
            if b["show_page_numbers"]:
                self.setFont(font, size)
                label = _clean(b["page_number_format"]).replace("{page}", str(self._pageNumber)).replace(
                    "{pages}", str(total))
                self.drawRightString(area_right, bottom + len(lines) * (size + 4) + 6, label)

        def _footer(self, total):
            if b["template"] == "footer_contact":
                return self._footer_inline(total)
            area_left, area_right = left, page_w - right
            footer_h = b["footer_height_mm"] * mm
            y_base = bottom
            size = b["footer_font_size"]
            self.setStrokeColor(secondary)
            self.setLineWidth(0.5)
            self.line(area_left, y_base + footer_h, area_right, y_base + footer_h)
            self.setFillColor(secondary)
            self.setFont(font, size)
            y = y_base + footer_h - size - 3
            for text in filter(None, (_footer_text(snap),)):
                for line in _wrap(_clean(text), self, font, size, area_right - area_left):
                    self.drawString(_x_for(b["footer_alignment"], area_left, area_right,
                                           self.stringWidth(line, font, size)), y, line)
                    y -= size + 2
            if b["show_page_numbers"]:
                label = _clean(b["page_number_format"]).replace("{page}", str(self._pageNumber)).replace(
                    "{pages}", str(total))
                self.drawString(_x_for(b["footer_alignment"], area_left, area_right,
                                       self.stringWidth(label, font, size)), y_base + 2, label)

    return DecoratedCanvas


def _wrap(text, canv, font, size, width):
    lines, current = [], ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if canv.stringWidth(trial, font, size) <= width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines[:3]


def _styles(snap):
    b = snap["branding"]
    font, bold = b["font_family"], BOLD[b["font_family"]]
    primary = colors.HexColor(b["primary_colour"])
    spacing = float(b.get("line_spacing") or 1.4)
    base = ParagraphStyle("base", fontName=font, fontSize=b["body_font_size"], leading=b["body_font_size"] * spacing)
    return {
        "base": base,
        # Letter body paragraphs: optionally justified.
        "body": ParagraphStyle("body", parent=base, alignment=TA_JUSTIFY if b.get("justify_body") else TA_LEFT),
        "title": ParagraphStyle("title", parent=base, fontName=bold, fontSize=b["heading_font_size"],
                                leading=b["heading_font_size"] * 1.3, textColor=primary, spaceAfter=6),
        "h2": ParagraphStyle("h2", parent=base, fontName=bold, textColor=primary, spaceBefore=8, spaceAfter=3),
        "small": ParagraphStyle("small", parent=base, fontSize=max(b["body_font_size"] - 1.5, 6.5),
                                leading=max(b["body_font_size"] - 1.5, 6.5) * 1.35,
                                textColor=colors.HexColor(b["secondary_colour"])),
        "bold": ParagraphStyle("bold", parent=base, fontName=bold),
        "sec_h": ParagraphStyle("sec_h", parent=base, fontName=bold, textColor=primary, spaceBefore=10, spaceAfter=3),
        # Terms and conditions: a size down from the body text but in the same dark colour, so they stay readable.
        "terms": ParagraphStyle("terms", parent=base, fontSize=max(b["body_font_size"] - 1, 7),
                                leading=max(b["body_font_size"] - 1, 7) * spacing,
                                alignment=TA_JUSTIFY if b.get("justify_body") else TA_LEFT),
        "right": ParagraphStyle("right", parent=base, alignment=TA_RIGHT),
        "cell": ParagraphStyle("cell", parent=base, fontSize=b["body_font_size"] - 0.5,
                               leading=(b["body_font_size"] - 0.5) * 1.3),
        "cell_right": ParagraphStyle("cell_right", parent=base, fontSize=b["body_font_size"] - 0.5,
                                     leading=(b["body_font_size"] - 0.5) * 1.3, alignment=TA_RIGHT),
        "cell_head": ParagraphStyle("cell_head", parent=base, fontName=bold, fontSize=b["body_font_size"] - 0.5,
                                    textColor=colors.white),
    }


def _meta_table(snap, st, reference_number, issue_date, extra):
    rows = []
    if reference_number:
        rows.append([Paragraph("Reference No.", st["small"]), Paragraph(_para(reference_number), st["bold"])])
    rows.append([Paragraph("Date", st["small"]), Paragraph(issue_date.strftime("%d %B %Y"), st["base"])])
    for label, value in extra or []:
        rows.append([Paragraph(_para(label), st["small"]), Paragraph(_para(value), st["base"])])
    table = Table(rows, colWidths=[32 * mm, None], hAlign="LEFT")
    table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                               ("TOPPADDING", (0, 0), (-1, -1), 1), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    return table


def _items_table(snap, st, spec, width):
    b = snap["branding"]
    columns = spec["columns"]
    numeric = set(spec.get("numeric_columns", []))
    data = [[Paragraph(_para(c), st["cell_head"]) for c in columns]]
    for row in spec["rows"]:
        data.append([
            Paragraph(_para(v), st["cell_right"] if i in numeric else st["cell"]) for i, v in enumerate(row)
        ])
    widths = spec.get("column_widths_mm")
    col_widths = [None if w is None else w * mm for w in widths] if widths else None
    if col_widths and None in col_widths:
        # One flexible column (None) takes whatever width the fixed ones leave, but never less than 30 mm.
        fixed = sum(w for w in col_widths if w is not None)
        room = max(width - fixed, 30 * mm)
        scale = min(1.0, (width - room) / fixed) if fixed else 1.0
        col_widths = [room if w is None else w * scale for w in col_widths]
    table = Table(data, colWidths=col_widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(b["primary_colour"])),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#C9D1DB")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5F7FA")]),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return table


def _totals_table(snap, st, totals):
    rows = [[Paragraph(_para(label), st["cell_right"]), Paragraph(_para(value), st["cell_right"])]
            for label, value in totals]
    table = Table(rows, colWidths=[None, 38 * mm], hAlign="RIGHT")
    last = len(rows) - 1
    table.setStyle(TableStyle([
        ("LINEABOVE", (0, last), (-1, last), 0.75, colors.HexColor(snap["branding"]["primary_colour"])),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    return table


def _details_table(snap, st, rows, width):
    """Key details as a label/value grid (project, client, completion period ...)."""
    data = [[Paragraph(f"<b>{_para(label)}</b>", st["cell"]), Paragraph(_para(value), st["cell"])] for label, value in rows]
    table = Table(data, colWidths=[min(55 * mm, width * 0.32), None], hAlign="LEFT")
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#C9D1DB")),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F5F7FA")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return table


def _signature_block(snap, st, signature_bytes):
    block = snap["branding"]["signature_block"]
    if not block.get("show"):
        return None
    signatory = snap.get("signatory") or {}
    company_name = snap["company"]["display_name"]
    parts = [Paragraph(f"For {_para(company_name)}", st["bold"])]
    if block.get("show_signature_image") and signature_bytes:
        from reportlab.platypus import Image
        img = ImageReader(io.BytesIO(signature_bytes))
        iw, ih = img.getSize()
        h = min(block["space_mm"] * mm, 25 * mm)
        parts.append(Image(io.BytesIO(signature_bytes), width=h * iw / ih, height=h, hAlign="LEFT"))
    else:
        parts.append(Spacer(1, block["space_mm"] * mm))
    parts.append(Paragraph(_para(block.get("label", "Authorised Signatory")), st["small"]))
    if block.get("show_name") and signatory.get("name"):
        parts.append(Paragraph(_para(signatory["name"]), st["base"]))
    if block.get("show_designation") and signatory.get("designation"):
        parts.append(Paragraph(_para(signatory["designation"]), st["small"]))
    return KeepTogether(parts)


ATTENDANCE_LABELS = {"present": "Present", "absent": "Absent", "apologies": "Apologies"}


def _grid_table(snap, st, header, rows, widths_mm):
    """Plain grid used for the attendee and action tables of the minutes."""
    data = [[Paragraph(_para(h), st["cell_head"]) for h in header]]
    data += [[Paragraph(_para(c), st["cell"]) for c in row] for row in rows]
    table = Table(data, colWidths=[w * mm for w in widths_mm], repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(snap["branding"]["primary_colour"])),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#C9D1DB")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return table


def _minutes_flowables(snap, st, minutes, gap, frame_w):
    """Attendees, agenda, discussion and decisions, action items, next meeting and the space for the seal."""
    out = []
    width_mm = frame_w / mm
    attendees = minutes.get("attendees") or []
    if attendees:
        out.append(Paragraph("Attendees", st["h2"]))
        rows = [[str(i), a.get("name", ""), a.get("designation", ""),
                 ATTENDANCE_LABELS.get(a.get("status", "present"), "Present")] for i, a in enumerate(attendees, 1)]
        out.append(_grid_table(snap, st, ["No.", "Name", "Designation / organisation", "Attendance"], rows,
                               [12, width_mm * 0.38, width_mm * 0.38 - 12, width_mm * 0.24 - 0]))
    agenda = minutes.get("agenda") or []
    if agenda:
        out.append(Paragraph("Agenda", st["h2"]))
        for i, text in enumerate(agenda, 1):
            out.append(Paragraph(f"{i}. {_para(text)}", st["body"]))
    items = minutes.get("items") or []
    if items:
        out.append(Paragraph("Discussion and decisions", st["h2"]))
        for i, item in enumerate(items, 1):
            block = [Paragraph(f"<b>{i}. {_para(item.get('heading', ''))}</b>", st["base"])]
            if item.get("discussion"):
                block.append(Paragraph(_para(item["discussion"]), st["body"]))
            if item.get("decision"):
                block.append(Paragraph(f"<b>Decision:</b> {_para(item['decision'])}", st["body"]))
            block.append(Spacer(1, gap))
            out.append(KeepTogether(block))
    actions = minutes.get("actions") or []
    if actions:
        out.append(Paragraph("Action items", st["h2"]))
        def due(value):
            try:
                return datetime.date.fromisoformat(value).strftime("%d %b %Y")
            except (TypeError, ValueError):
                return value or ""

        rows = [[str(i), a.get("action", ""), a.get("owner", ""), due(a.get("due_date"))] for i, a in enumerate(actions, 1)]
        out.append(_grid_table(snap, st, ["No.", "Action", "Owner", "Due date"], rows,
                               [12, width_mm * 0.55, width_mm * 0.2, width_mm * 0.25 - 12]))
    tail = []  # kept together with the seal so the seal never sits alone on a page
    if minutes.get("next_meeting"):
        tail.append(Spacer(1, 6))
        tail.append(Paragraph(f"<b>Next meeting:</b> {_para(minutes['next_meeting'])}", st["base"]))
    # The company seal is stamped on the printed page, so there are no signature lines: only the space is kept,
    # at the right, with a small caption. Nothing is drawn that would print over the stamp.
    seal_caption = ParagraphStyle("seal", parent=st["small"], alignment=TA_CENTER)
    seal = Table([["", [Spacer(1, 28 * mm), Paragraph("Company seal", seal_caption)]]],
                 colWidths=[frame_w - 60 * mm, 60 * mm], hAlign="LEFT")
    seal.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "BOTTOM"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    tail += [Spacer(1, 8), seal]
    out.append(KeepTogether(tail))
    return out


def render_pdf(snapshot, content, *, title, reference_number="", issue_date, logo_bytes=None,
               signature_bytes=None, draft=False, watermark_bytes=None, variant="letterhead"):
    """Render a PDF and return its bytes.

    variant="letterhead" draws the full letterhead; variant="plain" leaves it out for printing on
    pre-printed official letterhead paper (same margins, same text positions).

    `content` keys (all optional): recipient (list of lines), subject, paragraphs (list),
    sections (list of {heading, body}), closing_sections (same, under the totals), after_totals (list of text),
    details (list of [label, value]), show_signature (False hides the signature block),
    Text fields accept light Markdown (see richtext.py).
    meta (list of [label, value]), table {columns, rows, numeric_columns, column_widths_mm},
    totals (list of [label, value]), terms (str), include_bank (bool).
    """
    b = snapshot["branding"]
    page_w, page_h = PAGE_SIZES[b["page_size"]]
    top, right, bottom, left = (m * mm for m in b["margins_mm"])
    header_h, footer_h = b["header_height_mm"] * mm, b["footer_height_mm"] * mm
    frame_w = page_w - left - right
    st = _styles(snapshot)

    story = [Paragraph(_para(title), st["title"]), _meta_table(snapshot, st, reference_number, issue_date,
                                                            content.get("meta")), Spacer(1, 6)]
    if content.get("recipient"):
        story.append(Paragraph("To", st["small"]))
        story.append(Paragraph(_para("\n".join(content["recipient"])), st["base"]))
        story.append(Spacer(1, 6))
    if content.get("subject"):
        story.append(Paragraph(f"<b>Subject: {_para(content['subject'])}</b>", st["base"]))
        story.append(Spacer(1, 6))
    gap = b.get("paragraph_spacing_pt", 6)
    minutes = content.get("minutes")
    if minutes:
        story.extend(_minutes_flowables(snapshot, st, minutes, gap, frame_w))
    primary = colors.HexColor(b["primary_colour"])

    def rich(text, base=st["body"]):
        # Pasted Markdown (headings, **bold**, lists, tables) becomes real structure; see services/richtext.py.
        return richtext.flowables(text, st, base=base, gap=gap, width=frame_w, primary=primary)

    def heading(text):
        # A heading never ends a page on its own, yet never drags a whole table or long text to the next page.
        return [CondPageBreak(28 * mm), Paragraph(_para(text), st["sec_h"])]

    def titled(section):
        return (heading(section["heading"]) if section.get("heading") else []) + rich(section.get("body", ""))

    for text in content.get("paragraphs", []):
        story.extend(rich(text))
    for section in content.get("sections", []):
        story.extend(titled(section))
    if content.get("table"):
        story.append(Spacer(1, 4))
        story.append(_items_table(snapshot, st, content["table"], frame_w))
    if content.get("totals"):
        story.append(Spacer(1, 4))
        story.append(_totals_table(snapshot, st, content["totals"]))
    for text in content.get("after_totals", []):
        story.append(Spacer(1, 4))
        story.extend(rich(text))
    if content.get("details"):
        story.extend(heading("Key details"))
        story.append(_details_table(snapshot, st, content["details"], frame_w))
        story.append(Spacer(1, gap))
    for section in content.get("closing_sections", []):
        story.extend(titled(section))  # scope, deliverables, timeline ... printed under the totals

    bank = snapshot.get("bank") if content.get("include_bank") else None
    if bank:
        story.append(Paragraph("Payment details", st["h2"]))
        story.append(Paragraph(_para(
            f"Account name: {bank['holder']}\nBank: {bank['bank_name']}"
            + (f", {bank['branch']}" if bank["branch"] else "")
            + f"\nAccount number: {bank['account_number_masked']}\nIFSC: {bank['ifsc']}"), st["base"]))
    terms = content.get("terms") or ""
    if terms:
        story.extend(heading("Terms and conditions"))
        story.extend(rich(terms, st["terms"]))
    notice = (snapshot.get("policy") or {}).get("confidentiality_notice")
    if notice and content.get("include_confidentiality"):
        story.append(Spacer(1, 6))
        story.append(Paragraph(_para(notice), st["small"]))
    # No "For <company> / Authorised Signatory" block on minutes (the seal goes there) or when the document says so.
    signature = None if (minutes or content.get("show_signature") is False) else _signature_block(snapshot, st, signature_bytes)
    if signature is not None:
        story.append(Spacer(1, 14))
        story.append(signature)

    buffer = io.BytesIO()
    doc = BaseDocTemplate(
        buffer, pagesize=(page_w, page_h), leftMargin=left, rightMargin=right, topMargin=top,
        bottomMargin=bottom, title=_clean(title), author=_clean(snapshot["company"]["legal_name"]),
        creator="Tera2Nano ERP",
    )
    frame = Frame(
        left, bottom + footer_h + 3 * mm, frame_w, page_h - top - bottom - header_h - footer_h - 6 * mm,
        leftPadding=0, rightPadding=0, topPadding=b.get("body_top_gap_mm", 3) * mm, bottomPadding=0, id="body",
    )
    doc.addPageTemplates([PageTemplate(id="letterhead", frames=[frame], onPage=lambda canv, _doc: canv.underlay())])
    doc.build(story, canvasmaker=_decorator_canvas(snapshot, logo_bytes, draft, watermark_bytes,
                                                   plain=variant == "plain"))
    return buffer.getvalue()
