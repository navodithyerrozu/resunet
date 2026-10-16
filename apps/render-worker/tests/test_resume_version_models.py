from __future__ import annotations

from datetime import UTC, datetime

from worker.context import build_context
from worker.latex import render_template
from worker.models import ResumeVersionRead


def test_profile_education_and_certification_dates_render_from_version_snapshot() -> None:
    version = ResumeVersionRead.model_validate(
        {
            "_id": "111111111111111111111111",
            "resume_id": "222222222222222222222222",
            "user_id": "333333333333333333333333",
            "version": 1,
            "template": "ats_classic",
            "contact": {"full_name": "Resume Tester", "email": "test@example.com"},
            "education": [
                {
                    "institution": "Example University",
                    "degree": None,
                    "field_of_study": "Computer Science",
                    "dates": {
                        "start": datetime(2020, 1, 1, tzinfo=UTC),
                        "end": datetime(2024, 5, 1, tzinfo=UTC),
                    },
                    "gpa": 3.8,
                }
            ],
            "certifications": [
                {
                    "name": "Cloud Practitioner",
                    "issuer": "Example Cloud",
                    "issued_on": datetime(2024, 2, 1, tzinfo=UTC),
                    "expires_on": datetime(2027, 2, 1, tzinfo=UTC),
                }
            ],
        }
    )

    context = build_context(version)
    assert context["education"][0].date_label == "Jan 2020 – May 2024"
    assert context["certifications"][0].date_label == "Feb 2024 – Feb 2027"

    latex = render_template("ats_classic.tex.j2", context)
    assert "Jan 2020 -- May 2024" in latex
    assert "Cloud Practitioner --- Example Cloud (Feb 2024 -- Feb 2027)" in latex
    assert "None" not in latex
