"""ordered_sections must not return sections the template renders separately.

Summary, skills, education, and certifications have dedicated blocks in
_base.tex.j2. Returning them here as well printed each heading twice, and
printed empty Summary/Skills headings on resumes with no summary or skills.
"""

from worker.models import ResumeVersionRead


def _version(sections):
    return ResumeVersionRead(
        _id="68f000000000000000000001",
        resume_id="6addefe259b9215632887af4",
        user_id="6acf0ac4f19d82c25f99eb3b",
        version=1,
        template="ats_classic",
        contact={"full_name": "Test User", "email": "test.user@example.com"},
        summary="A summary.",
        sections=sections,
        skills=[{"name": "Python", "category": "Languages"}],
        education=[],
        certifications=[],
    )


def _entry(entry_id="e1"):
    return {
        "entry_id": entry_id,
        "heading": "Heading",
        "date_label": "2025",
        "bullets": [],
    }


def test_separately_rendered_kinds_are_excluded():
    sections = [
        {"kind": "summary", "title": "Summary", "order": 0, "entries": []},
        {"kind": "experience", "title": "Experience", "order": 1, "entries": [_entry("e1")]},
        {"kind": "projects", "title": "Projects", "order": 2, "entries": [_entry("p1")]},
        {"kind": "education", "title": "Education", "order": 3, "entries": [_entry("ed1")]},
        {"kind": "skills", "title": "Skills", "order": 4, "entries": []},
        {"kind": "certifications", "title": "Certifications", "order": 5, "entries": [_entry("c1")]},
    ]
    result = _version(sections).ordered_sections()
    assert [s.kind for s in result] == ["experience", "projects"]


def test_empty_experience_or_projects_sections_are_dropped():
    sections = [
        {"kind": "experience", "title": "Experience", "order": 1, "entries": []},
        {"kind": "projects", "title": "Projects", "order": 2, "entries": [_entry("p1")]},
    ]
    result = _version(sections).ordered_sections()
    assert [s.kind for s in result] == ["projects"]
