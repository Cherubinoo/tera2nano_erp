"""Pasted text -> structured PDF content.

Text pasted from documents and AI tools arrives with light Markdown: `##` headings, `**bold**`, `-` and `1.` lists,
`| tables |` and `---` rules. It is turned into real headings, lists and tables instead of printing the symbols.
Only this small subset is understood; anything else prints as the plain text it is. Single line breaks are kept and a
blank line starts a new paragraph.

The built-in PDF fonts are Latin-1 only, so the rupee sign is printed as "Rs." and other unsupported characters as "?".
"""
import re
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import CondPageBreak, HRFlowable, Paragraph, Spacer, Table, TableStyle

_RUPEE = re.compile("₹\\s*")
HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
RULE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
BULLET = re.compile(r"^(\s*)[-*+•]\s+(.*)$")
NUMBER = re.compile(r"^(\s*)(\d{1,3})[.)]\s+(.*)$")
TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
SEPARATOR_CELL = re.compile(r"^:?-{2,}:?$")
NUMERIC_CELL = re.compile(r"^[^\w]*(rs\.?|inr)?[\s\d.,/-]*\d[\d.,\s/-]*%?$", re.IGNORECASE)


def clean(text):
    """Latin-1 safe text: the rupee sign becomes "Rs. ", anything else unsupported becomes "?"."""
    text = _RUPEE.sub("Rs. ", str(text if text is not None else ""))
    return text.encode("cp1252", "replace").decode("cp1252")


def inline(text):
    """Escaped markup for one line: **bold** and *italic*; backticks are dropped."""
    s = escape(clean(text))
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?![*\w])", r"<i>\1</i>", s)
    return re.sub(r"`([^`]+)`", r"\1", s)


def _paragraph(markup, style, plain, **kw):
    try:
        return Paragraph(markup, style, **kw)
    except Exception:  # markup the parser rejects prints as plain text rather than failing the document
        return Paragraph(escape(clean(plain)), style, **kw)


def _table(rows, st, base, width, primary):
    """A Markdown table. A separator row (|---|:---:|) after the first row makes that row the header."""
    cells = [[c.strip() for c in row.strip().strip("|").split("|")] for row in rows]
    header = None
    if len(cells) > 1 and all(SEPARATOR_CELL.match(c) for c in cells[1]):
        header, cells = cells[0], cells[2:]
    count = max(len(r) for r in ([header] if header else []) + cells) if (header or cells) else 0
    if not count:
        return None
    pad = lambda r: r + [""] * (count - len(r))
    body = [pad(r)[:count] for r in cells]
    head = pad(header)[:count] if header else None
    plain = lambda s: s.replace("**", "")
    numeric = [bool(body) and all(NUMERIC_CELL.match(plain(r[i]).strip()) for r in body if r[i].strip()) and
               any(r[i].strip() for r in body) for i in range(count)]
    small = ParagraphStyle("md_cell", parent=base, fontSize=base.fontSize - 0.5, leading=(base.fontSize - 0.5) * 1.3)
    right = ParagraphStyle("md_cell_r", parent=small, alignment=TA_RIGHT)
    head_style = ParagraphStyle("md_head", parent=small, fontName=st["bold"].fontName, textColor=colors.white)
    head_right = ParagraphStyle("md_head_r", parent=head_style, alignment=TA_RIGHT)
    data = []
    if head:
        data.append([_paragraph(inline(c), head_right if numeric[i] else head_style, c) for i, c in enumerate(head)])
    for r in body:
        data.append([_paragraph(inline(c), right if numeric[i] else small, c) for i, c in enumerate(r)])
    # Columns share the width in proportion to their longest text, within sensible limits.
    weights = [min(60, max(8, max((len(plain(r[i])) for r in ([head] if head else []) + body), default=8)))
               for i in range(count)]
    col_widths = [width * w / sum(weights) for w in weights]
    table = Table(data, colWidths=col_widths, repeatRows=1 if head else 0, hAlign="LEFT")
    style = [("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#C9D1DB")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
             ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
    if head:
        style.append(("BACKGROUND", (0, 0), (-1, 0), primary))
        style.append(("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5F7FA")]))
    table.setStyle(TableStyle(style))
    return table


def flowables(text, st, *, base, gap, width, primary):
    """Flowables for `text`. `st` is the style set from pdf._styles; `base` is the paragraph style for body text."""
    heading_big = ParagraphStyle("md_h", parent=st["h2"])
    heading_small = ParagraphStyle("md_h3", parent=st["bold"], spaceBefore=6, spaceAfter=2)
    lines = clean(text).replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out, para, i = [], [], 0

    def flush():
        if para:
            out.append(_paragraph("<br/>".join(inline(p) for p in para), base, "\n".join(para)))
            out.append(Spacer(1, gap))
            para.clear()

    while i < len(lines):
        line = lines[i].rstrip()
        if not line.strip():
            flush()
            i += 1
            continue
        heading = HEADING.match(line)
        if heading:
            flush()
            style = heading_big if len(heading.group(1)) <= 2 else heading_small
            out.append(CondPageBreak(28 * mm))  # a heading never ends a page on its own, yet never drags a table along
            out.append(_paragraph(inline(heading.group(2)), style, heading.group(2)))
            i += 1
            continue
        if RULE.match(line):
            flush()
            out.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#C9D1DB"), spaceBefore=3, spaceAfter=5))
            i += 1
            continue
        if TABLE_ROW.match(line):
            flush()
            rows = []
            while i < len(lines) and TABLE_ROW.match(lines[i].rstrip()):
                rows.append(lines[i].rstrip())
                i += 1
            table = _table(rows, st, base, width, primary)
            if table is not None:
                out += [table, Spacer(1, gap)]
            continue
        if BULLET.match(line) or NUMBER.match(line):
            flush()
            items = []  # [indent, bullet, text]
            while i < len(lines):
                current = lines[i].rstrip()
                bullet, number = BULLET.match(current), NUMBER.match(current)
                if bullet or number:
                    indent = len((bullet or number).group(1).replace("\t", "  "))
                    items.append([indent, "•" if bullet else f"{number.group(2)}.", (bullet or number).groups()[-1]])
                elif current.strip() and items and current.startswith((" ", "\t")) and not HEADING.match(current) \
                        and not TABLE_ROW.match(current):
                    items[-1][2] += "\n" + current.strip()  # an indented continuation of the previous item
                else:
                    break
                i += 1
            for indent, mark, body in items:
                level = min(indent // 2, 3)
                item_style = ParagraphStyle("md_li", parent=base, leftIndent=16 + 14 * level, bulletIndent=3 + 14 * level,
                                            spaceAfter=2, alignment=0)
                out.append(_paragraph("<br/>".join(inline(p) for p in body.split("\n")), item_style, body,
                                      bulletText=clean(mark)))
            out.append(Spacer(1, gap))
            continue
        para.append(line)
        i += 1
    flush()
    return out
