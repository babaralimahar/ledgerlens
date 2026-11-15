"""Ten invoice layouts rendered with reportlab. Each varies labels, date and money formats,
header placement, table style and which optional fields are printed.

``render`` returns the PDF bytes plus a map of how every printed value appears, so ground
truth knows the exact strings (used to verify evidence quotes)."""

from __future__ import annotations

import io
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from reportlab import rl_config
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

rl_config.invariant = 1  # no timestamps or random ids: same input -> byte-identical PDF

MoneyFmt = Callable[[Decimal, str], str]


def _comma(x: Decimal, _cur: str) -> str:
    return f"{x:,.2f}"


def _symbol(x: Decimal, cur: str) -> str:
    return f"$ {x:,.2f}" if cur == "USD" else f"{cur} {x:,.2f}"


def _suffix(x: Decimal, cur: str) -> str:
    return f"{x:,.2f} {cur}"


def _plain(x: Decimal, _cur: str) -> str:
    return f"{x:.2f}"


def _space(x: Decimal, _cur: str) -> str:
    return f"{x:,.2f}".replace(",", " ")


def fmt_qty(q: Decimal) -> str:
    return str(int(q)) if q == q.to_integral_value() else f"{q.normalize()}"


@dataclass(frozen=True)
class Layout:
    name: str
    title: str
    labels: dict[str, str]
    date_fmt: str
    money: MoneyFmt
    header: Literal["left", "right", "table"]
    table: Literal["grid", "zebra", "lines", "plain"]
    ref: Literal["none", "line", "sku"]
    show_tax_id: bool
    show_due: bool
    font: str
    pagesize: tuple[float, float] = A4


_BASE = {
    "invoice_number": "Invoice No:",
    "invoice_date": "Invoice Date:",
    "due_date": "Due Date:",
    "po_number": "PO Number:",
    "tax_id": "Tax ID:",
    "currency": "Currency:",
    "subtotal": "Subtotal",
    "tax": "Tax",
    "total": "Total",
}


def _labels(**over: str) -> dict[str, str]:
    return {**_BASE, **over}


LAYOUTS: tuple[Layout, ...] = (
    Layout(
        "classic",
        "INVOICE",
        _labels(),
        "%Y-%m-%d",
        _comma,
        "left",
        "grid",
        "line",
        True,
        True,
        "Helvetica",
    ),
    Layout(
        "modern",
        "Tax Invoice",
        _labels(
            invoice_number="Invoice #",
            invoice_date="Date",
            due_date="Payment due",
            po_number="Your PO",
        ),
        "%d %b %Y",
        _symbol,
        "right",
        "zebra",
        "sku",
        True,
        True,
        "Helvetica",
    ),
    Layout(
        "ledger",
        "BILL",
        _labels(
            invoice_number="Bill Number",
            invoice_date="Bill Date",
            po_number="Purchase Order",
            total="Amount Due",
        ),
        "%d/%m/%Y",
        _suffix,
        "table",
        "lines",
        "none",
        True,
        True,
        "Times-Roman",
    ),
    Layout(
        "compact",
        "Invoice",
        _labels(
            invoice_number="Inv.", invoice_date="Dated", due_date="Due", po_number="PO#", tax="VAT"
        ),
        "%b %d, %Y",
        _plain,
        "left",
        "plain",
        "line",
        False,
        True,
        "Courier",
        LETTER,
    ),
    Layout(
        "statement",
        "INVOICE / STATEMENT",
        _labels(
            invoice_number="Document No.",
            po_number="Order Ref",
            tax="Sales Tax",
            total="Grand Total",
        ),
        "%d-%b-%Y",
        _comma,
        "right",
        "grid",
        "sku",
        True,
        False,
        "Helvetica",
    ),
    Layout(
        "euro",
        "Rechnung / Invoice",
        _labels(
            invoice_number="Invoice Number", invoice_date="Issue Date", tax="VAT", tax_id="VAT No:"
        ),
        "%d.%m.%Y",
        _space,
        "table",
        "zebra",
        "line",
        True,
        True,
        "Helvetica",
    ),
    Layout(
        "service",
        "Service Invoice",
        _labels(
            invoice_number="Reference",
            po_number="Client PO",
            subtotal="Net Amount",
            total="Total Payable",
        ),
        "%B %d, %Y",
        _suffix,
        "left",
        "lines",
        "none",
        True,
        True,
        "Times-Roman",
        LETTER,
    ),
    Layout(
        "wholesale",
        "COMMERCIAL INVOICE",
        _labels(invoice_number="Invoice No.", po_number="P.O. No.", tax_id="NTN:"),
        "%d/%m/%Y",
        _comma,
        "right",
        "grid",
        "line",
        True,
        True,
        "Helvetica",
    ),
    Layout(
        "minimal",
        "invoice",
        _labels(
            invoice_number="no.",
            invoice_date="date",
            due_date="due",
            po_number="po",
            subtotal="subtotal",
            tax="tax",
            total="total",
            currency="currency:",
            tax_id="tax id:",
        ),
        "%Y/%m/%d",
        _plain,
        "left",
        "plain",
        "sku",
        False,
        False,
        "Courier",
    ),
    Layout(
        "boxed",
        "TAX INVOICE",
        _labels(
            invoice_number="Invoice Ref",
            invoice_date="Invoice Date",
            due_date="Pay By",
            po_number="PO Reference",
        ),
        "%d %B %Y",
        _symbol,
        "table",
        "grid",
        "line",
        True,
        True,
        "Helvetica-Bold",
    ),
)
LAYOUT_BY_NAME = {lay.name: lay for lay in LAYOUTS}


@dataclass(frozen=True)
class RenderLine:
    description: str
    quantity: Decimal
    unit_price: Decimal
    amount: Decimal
    line_no: int
    sku: str


@dataclass(frozen=True)
class RenderInvoice:
    vendor_name: str
    vendor_address: str
    vendor_tax_id: str
    invoice_number: str
    invoice_date: date
    due_date: date
    po_number: str | None
    currency: str
    tax_rate: Decimal
    subtotal: Decimal
    tax: Decimal
    total: Decimal
    lines: list[RenderLine]
    note: str | None = None  # visible free text (used by injection fixtures)
    hidden_note: str | None = None  # white text (hidden-text fixture)


def render(inv: RenderInvoice, lay: Layout) -> tuple[bytes, dict[str, str]]:
    """Render one invoice. Returns (pdf bytes, rendered strings by field name)."""
    rendered: dict[str, str] = {}
    lab = lay.labels
    bold = (
        "Helvetica-Bold"
        if lay.font.startswith("Helvetica")
        else ("Times-Bold" if lay.font.startswith("Times") else "Courier-Bold")
    )
    body = ParagraphStyle("b", fontName=lay.font, fontSize=9.5, leading=12)
    big = ParagraphStyle("t", fontName=bold, fontSize=16, leading=20)
    name_style = ParagraphStyle("n", fontName=bold, fontSize=12, leading=15)

    def money(x: Decimal) -> str:
        return lay.money(x, inv.currency)

    def put(key: str, value: str) -> str:
        rendered[key] = value
        return value

    story: list[Any] = []
    vendor_block = [
        Paragraph(put("vendor_name", inv.vendor_name), name_style),
        Paragraph(inv.vendor_address, body),
    ]
    if lay.show_tax_id:
        vendor_block.append(
            Paragraph(f"{lab['tax_id']} {put('vendor_tax_id', inv.vendor_tax_id)}", body)
        )

    meta: list[tuple[str, str]] = [
        (lab["invoice_number"], put("invoice_number", inv.invoice_number)),
        (lab["invoice_date"], put("invoice_date", inv.invoice_date.strftime(lay.date_fmt))),
    ]
    if lay.show_due:
        meta.append((lab["due_date"], put("due_date", inv.due_date.strftime(lay.date_fmt))))
    if inv.po_number:
        meta.append((lab["po_number"], put("po_number", inv.po_number)))
    meta.append((lab["currency"], put("currency", inv.currency)))

    story.append(Paragraph(lay.title, big))
    story.append(Spacer(1, 4 * mm))
    if lay.header == "table":
        story += vendor_block
        story.append(Spacer(1, 3 * mm))
        mt = Table([[k, v] for k, v in meta], colWidths=[40 * mm, 60 * mm], hAlign="LEFT")
        mt.setStyle(
            TableStyle(
                [
                    ("FONT", (0, 0), (-1, -1), lay.font, 9.5),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                ]
            )
        )
        story.append(mt)
    else:
        meta_par = [Paragraph(f"{k} {v}", body) for k, v in meta]
        left, right = (vendor_block, meta_par) if lay.header == "left" else (meta_par, vendor_block)
        rows = max(len(left), len(right))
        grid = [
            [left[i] if i < len(left) else "", right[i] if i < len(right) else ""]
            for i in range(rows)
        ]
        story.append(Table(grid, colWidths=[95 * mm, 80 * mm], hAlign="LEFT"))
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph("Bill to: Northwind Supplies, 12 Harbour Road, Karachi", body))
    story.append(Spacer(1, 5 * mm))

    head = ["Description", "Qty", "Unit Price", "Amount"]
    if lay.ref != "none":
        head = ["PO Line" if lay.ref == "line" else "Item Code", *head]
    rows_tbl: list[list[str]] = [head]
    for i, ln in enumerate(inv.lines, start=1):
        cells = [
            put(f"lines[{i}].description", ln.description),
            put(f"lines[{i}].quantity", fmt_qty(ln.quantity)),
            put(f"lines[{i}].unit_price", f"{ln.unit_price:,.2f}"),
            put(f"lines[{i}].amount", f"{ln.amount:,.2f}"),
        ]
        if lay.ref != "none":
            ref = f"L{ln.line_no}" if lay.ref == "line" else ln.sku
            cells = [put(f"lines[{i}].po_line_ref", ref), *cells]
        rows_tbl.append(cells)
    widths = [86 * mm, 16 * mm, 28 * mm, 30 * mm]
    if lay.ref != "none":
        widths = [22 * mm, 66 * mm, 16 * mm, 26 * mm, 30 * mm]
    t = Table(rows_tbl, colWidths=widths, hAlign="LEFT", repeatRows=1)
    style: list[Any] = [
        ("FONT", (0, 0), (-1, -1), lay.font, 9),
        ("FONT", (0, 0), (-1, 0), bold, 9),
        ("ALIGN", (-3, 1), (-1, -1), "RIGHT"),
    ]
    if lay.table == "grid":
        style.append(("GRID", (0, 0), (-1, -1), 0.4, colors.grey))
    elif lay.table == "zebra":
        style += [
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#EEF2F7")]),
            ("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.black),
        ]
    elif lay.table == "lines":
        style += [("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.grey)]
    t.setStyle(TableStyle(style))
    story.append(t)
    story.append(Spacer(1, 4 * mm))

    pct = f"{(inv.tax_rate * 100).normalize():f}%"
    totals = [
        [lab["subtotal"], put("subtotal", money(inv.subtotal))],
        [f"{lab['tax']} ({pct})", put("tax", money(inv.tax))],
        [lab["total"], put("total", money(inv.total))],
    ]
    tt = Table(totals, colWidths=[40 * mm, 40 * mm], hAlign="RIGHT")
    tt.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, -1), lay.font, 10),
                ("FONT", (0, -1), (-1, -1), bold, 10),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("LINEABOVE", (0, -1), (-1, -1), 0.8, colors.black),
            ]
        )
    )
    story.append(tt)
    if inv.note:
        story.append(Spacer(1, 6 * mm))
        story.append(Paragraph(inv.note, body))
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph("Thank you for your business.", body))

    def on_page(canvas: Canvas, _doc: Any) -> None:
        if inv.hidden_note:
            canvas.saveState()
            canvas.setFillColor(colors.white)
            canvas.setFont(lay.font, 6)
            canvas.drawString(15 * mm, 12 * mm, inv.hidden_note)
            canvas.restoreState()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=lay.pagesize,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title=f"Invoice {inv.invoice_number}",
        author=inv.vendor_name,
        invariant=1,
    )
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return buf.getvalue(), rendered


def render_scan(seed_text: str) -> bytes:
    """An image-only 'scan': shapes, no text layer (drives the NEEDS_OCR path)."""
    buf = io.BytesIO()
    c = Canvas(buf, pagesize=A4, invariant=1)
    c.setFillColor(colors.HexColor("#F3F1EA"))
    c.rect(0, 0, A4[0], A4[1], fill=1, stroke=0)
    c.setFillColor(colors.HexColor("#555555"))
    y = A4[1] - 30 * mm
    for i, ch in enumerate(seed_text * 3):
        width = (ord(ch) % 40 + 30) * mm / 2
        c.rect(20 * mm, y, width, 2.5 * mm, fill=1, stroke=0)
        y -= 6 * mm
        if y < 30 * mm or i > 35:
            break
    c.showPage()
    c.save()
    return buf.getvalue()
