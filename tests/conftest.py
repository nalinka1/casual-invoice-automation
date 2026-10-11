"""Synthetic configuration and PDF: tests never load the real private template."""
import json
from pathlib import Path
import pymupdf
import pytest


def example_config(tmp_path):
    config = json.loads((Path(__file__).resolve().parents[1] / "config.example.json").read_text(encoding="utf-8"))
    source = tmp_path / "templates" / "example-invoice.pdf"
    source.parent.mkdir(exist_ok=True)
    with pymupdf.open() as doc:
        page = doc.new_page(width=596.04, height=842.52)
        for text, point in [("Example Service Provider", (54, 110)), ("INVOICE NO: 11", (432, 126)), ("DATE: 05/09/2026", (432, 145)),
                            ("Casual Services - Site One", (60, 368)), ("714", (444, 368)), ("Casual Services - Site Two", (60, 411)),
                            ("850.5", (444, 411)), ("TOTAL", (340, 683)), ("1564.5", (444, 683)), ("Example payment details", (72, 730))]:
            page.insert_text(point, text, fontsize=9.96)
        page.draw_rect(pymupdf.Rect(52, 298, 544, 690))
        doc.save(source)
    config["invoice"]["source_pdf"] = str(source)
    config["_root"] = str(tmp_path)
    return config


@pytest.fixture
def config(tmp_path):
    return example_config(tmp_path)
