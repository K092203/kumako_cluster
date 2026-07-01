"""Tests for collectors."""

from pathlib import Path

from review_artifact.collect import (
    collect_directory_logs,
    collect_files,
    is_sensitive,
    read_file_limited,
)


FIXTURES = Path(__file__).parent / "fixtures"


def test_is_sensitive_blocks_env():
    assert is_sensitive(".env") is True
    assert is_sensitive("config/.env") is True
    assert is_sensitive(".env", allow_sensitive=True) is False


def test_is_sensitive_blocks_pem_and_keys():
    assert is_sensitive("server.pem") is True
    assert is_sensitive("id_rsa") is True
    assert is_sensitive("api_token.txt") is True


def test_collect_directory_logs_includes_known_files(tmp_path):
    (tmp_path / "meta.json").write_text('{"ok": true}')
    (tmp_path / "stderr.txt").write_text("error line\n")
    bundle = collect_directory_logs(
        tmp_path,
        include=["meta.json", "stderr.txt", "missing.log"],
        max_file_bytes=1024,
        max_total_bytes=4096,
    )
    paths = {f.path for f in bundle.files}
    assert "meta.json" in paths or any("meta.json" in p for p in paths)
    assert any("missing.log" in n for n in bundle.notes)


def test_collect_files_skips_sensitive(tmp_path):
    secret = tmp_path / ".env"
    secret.write_text("SECRET=1")
    readme = tmp_path / "readme.txt"
    readme.write_text("hello")

    bundle = collect_files(
        [secret, readme],
        max_file_bytes=1024,
        max_total_bytes=4096,
        cwd=tmp_path,
    )
    assert len(bundle.files) == 1
    assert bundle.files[0].path == "readme.txt"
    assert any("sensitive" in n for n in bundle.notes)


def test_collect_files_allow_sensitive(tmp_path):
    secret = tmp_path / ".env"
    secret.write_text("SECRET=1")
    bundle = collect_files(
        [secret],
        max_file_bytes=1024,
        max_total_bytes=4096,
        allow_sensitive=True,
        cwd=tmp_path,
    )
    assert len(bundle.files) == 1


def test_read_file_truncation(tmp_path):
    big = tmp_path / "big.log"
    big.write_text("x" * 5000)
    collected = read_file_limited(big, max_file_bytes=100)
    assert collected.truncated is True
    assert len(collected.content) > 100


def test_collect_files_binary_skip(tmp_path):
    binary = tmp_path / "data.bin"
    binary.write_bytes(b"\x00\x01\x02\x03")
    bundle = collect_files(
        [binary],
        max_file_bytes=1024,
        max_total_bytes=4096,
        cwd=tmp_path,
    )
    assert len(bundle.files) == 0
    assert any("binary" in n for n in bundle.notes)
