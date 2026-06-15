from __future__ import annotations

import importlib
import os
from pathlib import Path


class OCRBackendUnavailable(RuntimeError):
    """Raised when the configured OCR Python package or binary is missing."""


class OCRRecognitionError(RuntimeError):
    """Raised when OCR cannot read a specific image."""


def _load_pillow_modules():
    try:
        image_module = importlib.import_module("PIL.Image")
        image_ops_module = importlib.import_module("PIL.ImageOps")
    except ModuleNotFoundError as exc:
        if exc.name == "PIL":
            raise OCRBackendUnavailable("Pillow is required for OCR preprocessing. Install pillow.") from exc
        raise
    return image_module, image_ops_module


def _load_pytesseract():
    try:
        return importlib.import_module("pytesseract")
    except ModuleNotFoundError as exc:
        if exc.name == "pytesseract":
            raise OCRBackendUnavailable(
                "pytesseract is required for OCR. Install pytesseract and the Tesseract OCR executable."
            ) from exc
        raise


def _configure_tesseract(pytesseract) -> str | None:
    configured_cmd = os.getenv("TESSERACT_CMD", "").strip()
    standard_cmd = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
    if configured_cmd:
        pytesseract.pytesseract.tesseract_cmd = configured_cmd
    elif standard_cmd.exists():
        pytesseract.pytesseract.tesseract_cmd = str(standard_cmd)

    tessdata_dir = os.getenv("TESSDATA_DIR", "").strip()
    standard_tessdata_dir = Path.home() / "AppData" / "Local" / "Tesseract-OCR" / "tessdata"
    if tessdata_dir:
        return tessdata_dir
    if standard_tessdata_dir.exists():
        return str(standard_tessdata_dir)
    return None


def preprocess_image_for_ocr(image_path: str):
    image_module, image_ops_module = _load_pillow_modules()
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"OCR image file does not exist: {path}")

    try:
        with image_module.open(path) as source:
            image = source.convert("L")
    except image_module.UnidentifiedImageError as exc:
        raise OCRRecognitionError(f"OCR image file is not a readable image: {path}") from exc

    image = image_ops_module.autocontrast(image)
    width, height = image.size
    max_side = max(width, height)
    if max_side < 1600:
        scale = 1600 / max_side
        image = image.resize((int(width * scale), int(height * scale)))
    return image.point(lambda pixel: 255 if pixel > 180 else 0)


def recognize_image_text(image_path: str, language: str = "rus+eng", psm: int = 6) -> str:
    pytesseract = _load_pytesseract()
    tessdata_dir = _configure_tesseract(pytesseract)
    image = preprocess_image_for_ocr(image_path)
    config_parts = [f"--psm {psm}"]
    if tessdata_dir is not None:
        config_parts.append(f"--tessdata-dir {tessdata_dir}")
    config = " ".join(config_parts)
    try:
        text = pytesseract.image_to_string(image, lang=language, config=config)
    except pytesseract.pytesseract.TesseractNotFoundError as exc:
        raise OCRBackendUnavailable(
            "Tesseract OCR executable was not found. Install Tesseract OCR and make it available in PATH."
        ) from exc
    except pytesseract.pytesseract.TesseractError as exc:
        raise OCRRecognitionError(f"Tesseract failed for image {image_path}: {exc}") from exc
    return text.strip()
