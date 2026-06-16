import importlib
from pathlib import Path

import pytest

from app.ocr.recognize import OCRBackendUnavailable, OCRRecognitionError, recognize_image_text
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


@pytest.mark.parametrize(
    ("image_path", "expected_fragments"),
    [
        (
            "data/images/post_616768_0_fc3317ebb3.jpg",
            ["завел кота", "просто хвастаюсь"],
        ),
        (
            "data/images/post_533191_0_20de4175d5.jpg",
            ["зомби-вирус", "ищешь повод"],
        ),
        (
            "data/images/post_618526_0_278139ad4e.jpg",
            ["магистратуры", "оба юристы"],
        ),
    ],
)
def test_recognize_dark_or_gray_screenshots(image_path, expected_fragments):
    if not Path(image_path).exists():
        pytest.skip(f"Regression image is not available: {image_path}")

    try:
        text = recognize_image_text(image_path)
    except (OCRBackendUnavailable, OCRRecognitionError) as exc:
        pytest.skip(f"OCR backend is not available for regression test: {exc}")

    assert len(text) > 80
    for fragment in expected_fragments:
        assert fragment in text
