from dataclasses import dataclass
import calendar
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import pymupdf
from .calendar_rules import billable_dates


@dataclass(frozen=True)
class SiteCharge:
    site_id: str
    dates: tuple[date, ...]
    amount: Decimal


def calculate(config, year, month):
    return tuple(SiteCharge(s["id"], days, (Decimal(s["daily_rate_aud"]) * len(days)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
                 for s in config["sites"] for days in [billable_dates(config, year, month, s["id"])])


def total(charges):
    return sum((s.amount for s in charges), Decimal("0.00"))


def invoice_filename(year, month, prefix="Example"):
    date(year, month, 1)  # Validate the billing period before constructing the name.
    if not prefix or any(ch in prefix for ch in '/\\:*?"<>|'):
        raise ValueError("Invalid invoice filename prefix")
    return f"{prefix}_{calendar.month_name[month].upper()}_{year % 100:02d}_INVOICE.pdf"


def fields(page):
    # Locate values by individual character geometry; retain labels and table rules.
    targets = {"number": ("INVOICE NO: 11", "11"), "date": ("DATE:", None),
               "site1": ("714", "714"), "site2": ("850.5", "850.5"), "total": ("1564.5", "1564.5")}
    found = {}
    for b in page.get_text("rawdict")["blocks"]:
        for line in b.get("lines", []):
            for span in line["spans"]:
                chars = span["chars"]
                text = "".join(ch["c"] for ch in chars)
                for key, (marker, value) in targets.items():
                    if (key in ("number", "date") and text.startswith(marker)) or text.strip() == marker:
                        start = text.index(value) if value else text.index(":") + 1
                        selected = chars[start:]
                        while selected and selected[0]["c"] == " ":
                            selected = selected[1:]
                        while selected and selected[-1]["c"] == " ":
                            selected = selected[:-1]
                        rect = pymupdf.Rect(selected[0]["bbox"])
                        for ch in selected[1:]:
                            rect |= pymupdf.Rect(ch["bbox"])
                        if key in found:
                            raise ValueError("Ambiguous template field")
                        found[key] = (rect, selected[0]["origin"], span["size"])
    if set(found) != set(targets):
        raise ValueError("Source PDF does not match verified August template")
    return found


def generate_pdf(config, charges, number, created, destination):
    if type(number) is not int or number < 1:
        raise ValueError("Invoice number must be a positive integer")
    if len(charges) != 2 or any(not s.amount.is_finite() or s.amount < 0 for s in charges):
        raise ValueError("Invalid invoice amounts")
    source = Path(config.get("_root", ".")) / config["invoice"]["source_pdf"]
    with pymupdf.open(source) as doc:
        if config["invoice"]["source_invoice_page"] != 1:
            raise ValueError("Only verified source page 1 is supported")
        doc.select([0])
        page = doc[0]
        areas = fields(page)
        values = {"number": str(number), "date": created.strftime("%d/%m/%Y"),
                  "site1": f"{charges[0].amount:.2f}", "site2": f"{charges[1].amount:.2f}", "total": f"{total(charges):.2f}"}
        embedded = next((doc.extract_font(item[0])[3] for item in page.get_fonts() if item[3].endswith("ArialMT") and doc.extract_font(item[0])[3]), None)
        font = pymupdf.Font(fontbuffer=embedded) if embedded else pymupdf.Font("helv")
        for key, (rect, origin, size) in areas.items():
            if font.text_length(values[key], fontsize=size) > (544 if key in ("site1", "site2", "total") else 580) - origin[0]:
                raise ValueError("Invoice value exceeds template field width")
            page.add_redact_annot(rect, fill=(1, 1, 1))
        page.apply_redactions(images=0, graphics=0)
        page.insert_font(fontname="invoiceArial", fontbuffer=font.buffer)
        for key, (_, origin, size) in areas.items():
            page.insert_text(origin, values[key], fontsize=size, fontname="invoiceArial")
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        doc.save(destination, garbage=4, deflate=True)
    return destination
