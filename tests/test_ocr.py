import importlib

import pytest

from app.ocr.recognize import OCRBackendUnavailable, recognize_image_text
from scripts.run_ocr import _join_ocr_chunks


def test_recognize_image_text_reports_missing_pytesseract(monkeypatch):
    original_import_module = importlib.import_module

    def fake_import_module(name):
        if name == "pytesseract":
            raise ModuleNotFoundError("No module named 'pytesseract'", name="pytesseract")
        return original_import_module(name)

    monkeypatch.setattr(importlib, "import_module", fake_import_module)

    with pytest.raises(OCRBackendUnavailable, match="pytesseract is required"):
        recognize_image_text("missing.png")


def test_join_ocr_chunks_strips_empty_fragments():
    assert _join_ocr_chunks([" first ", "", "second\n"]) == "first\n\nsecond"
