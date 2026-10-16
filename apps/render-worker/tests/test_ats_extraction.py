from __future__ import annotations

import pytest

from worker import ats


def test_malformed_pdf_error_does_not_echo_resume_text_or_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sensitive = "confidential resume text"
    events: list[tuple[str, dict[str, object]]] = []

    def fail_reader(_stream: object) -> None:
        raise ValueError(sensitive)

    class FakeLogger:
        def warning(self, event: str, **fields: object) -> None:
            events.append((event, fields))

    monkeypatch.setattr("pypdf.PdfReader", fail_reader)
    monkeypatch.setattr(ats, "logger", FakeLogger())

    result = ats.extract_text(b"malformed pdf")

    assert result.confidence == 0
    assert result.problems == ["PDF could not be parsed. Render the resume again and retry."]
    assert sensitive not in " ".join(result.problems)
    assert events == [("ats.extraction_failed", {"error_type": "ValueError"})]
