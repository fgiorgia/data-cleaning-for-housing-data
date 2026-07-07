"""Unit tests for scripts/remove_bom.py."""

from __future__ import annotations

from pathlib import Path

from scripts.remove_bom import transcode_no_bom

BOM: bytes = b"\xef\xbb\xbf"


def test_strips_leading_bom(tmp_path: Path) -> None:
    source: Path = tmp_path / "with_bom.csv"
    destination: Path = tmp_path / "clean.csv"
    source.write_bytes(BOM + b"UniqueID,ParcelID\n1,007 00 0 125.00\n")

    transcode_no_bom(str(source), str(destination))

    result: bytes = destination.read_bytes()
    assert not result.startswith(BOM)
    assert result == b"UniqueID,ParcelID\n1,007 00 0 125.00\n"


def test_file_without_bom_is_copied_unchanged(tmp_path: Path) -> None:
    source: Path = tmp_path / "no_bom.csv"
    destination: Path = tmp_path / "clean.csv"
    payload: bytes = b"a,b\n1,2\n"
    source.write_bytes(payload)

    transcode_no_bom(str(source), str(destination))

    assert destination.read_bytes() == payload


def test_crlf_line_endings_are_preserved(tmp_path: Path) -> None:
    source: Path = tmp_path / "crlf.csv"
    destination: Path = tmp_path / "clean.csv"
    source.write_bytes(BOM + b"a,b\r\n1,2\r\n")

    transcode_no_bom(str(source), str(destination))

    assert destination.read_bytes() == b"a,b\r\n1,2\r\n"


def test_non_ascii_content_survives(tmp_path: Path) -> None:
    source: Path = tmp_path / "unicode.csv"
    destination: Path = tmp_path / "clean.csv"
    text: str = "owner,città\nJosé,Nashville\n"
    source.write_bytes(BOM + text.encode("utf-8"))

    transcode_no_bom(str(source), str(destination))

    assert destination.read_text(encoding="utf-8") == text
