from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from worker import storage


def test_absolute_path_rejects_parent_traversal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        storage,
        "get_settings",
        lambda: SimpleNamespace(render_storage_dir=tmp_path / "renders"),
    )

    with pytest.raises(storage.UnsafePathError):
        storage.absolute_path("../../outside.pdf")


def test_absolute_path_rejects_symlink_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "renders"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.pdf").write_bytes(b"secret")
    link = root / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"directory symlinks are unavailable: {exc}")

    monkeypatch.setattr(
        storage,
        "get_settings",
        lambda: SimpleNamespace(render_storage_dir=root),
    )

    with pytest.raises(storage.UnsafePathError):
        storage.absolute_path("escape/secret.pdf")


def test_relative_pdf_path_rejects_unsafe_identifiers() -> None:
    with pytest.raises(storage.UnsafePathError):
        storage.relative_pdf_path(
            user_id="../other-user",
            resume_id="222222222222222222222222",
            version=1,
            sha256="a" * 64,
        )


@pytest.mark.asyncio
async def test_write_and_read_pdf_stay_inside_storage_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "renders"
    monkeypatch.setattr(
        storage,
        "get_settings",
        lambda: SimpleNamespace(render_storage_dir=root),
    )
    relative = storage.relative_pdf_path(
        user_id="111111111111111111111111",
        resume_id="222222222222222222222222",
        version=2,
        sha256="a" * 64,
    )
    content = b"%PDF-1.7\nlocal test bytes"

    path = await storage.write_pdf(relative, content)

    assert path == (root / relative).resolve()
    assert path.is_relative_to(root.resolve())
    assert await storage.read_pdf(relative) == content
