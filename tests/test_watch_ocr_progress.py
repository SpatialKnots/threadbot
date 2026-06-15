from pathlib import Path

from scripts.watch_ocr_progress import parse_ocr_log


def test_parse_ocr_log_reports_batch_progress(tmp_path: Path):
    log_path = tmp_path / "ocr.log"
    log_path.write_text(
        "\n".join(
            [
                "INFO Selected 23436 image(s) for OCR.",
                "INFO OCR image id=121 post_id=102 path=data\\images\\a.jpg",
                "INFO OCR extracted 84 character(s) from image id=121.",
                "INFO OCR image id=122 post_id=103 path=data\\images\\b.jpg",
                "INFO OCR extracted no text from image id=122.",
                "INFO Committed OCR progress after 100 processed image(s).",
            ]
        ),
        encoding="utf-8",
    )

    progress = parse_ocr_log(log_path)

    assert progress.selected == 23436
    assert progress.started_images == 2
    assert progress.finished_images == 2
    assert progress.recognized_images == 1
    assert progress.empty_images == 1
    assert progress.committed_images == 100
    assert progress.current_image_id == 122
    assert progress.current_post_id == 103


def test_parse_ocr_log_reports_summary(tmp_path: Path):
    log_path = tmp_path / "ocr.log"
    log_path.write_text(
        "INFO OCR summary: selected=10 recognized=7 empty=2 failed=1 dry_run=False\n",
        encoding="utf-8",
    )

    progress = parse_ocr_log(log_path)

    assert progress.summary_seen is True
    assert progress.selected == 10
    assert progress.finished_images == 10
    assert progress.failed_images == 1
