from __future__ import annotations

import tarfile
import zipfile

import pytest

from jarvis.safety import SafetyError, resolve_path, run_command
from jarvis.tools.files import extract_archive, list_dir, write_file


def test_home_confinement_rejects_outside_paths():
    with pytest.raises(SafetyError, match="outside the home directory"):
        resolve_path("/etc/passwd")


def test_home_confinement_rejects_symlink_escape(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    link = home / "link.txt"
    link.symlink_to(outside)
    with pytest.raises(SafetyError):
        resolve_path(str(link))


def test_write_requires_overwrite_flag(tmp_path):
    target = tmp_path / "f.txt"
    write_file(str(target), "one", confine_to_home=False)
    with pytest.raises(SafetyError, match="already exists"):
        write_file(str(target), "two", confine_to_home=False)
    write_file(str(target), "two", overwrite=True, confine_to_home=False)
    assert target.read_text() == "two"


def test_zip_traversal_is_blocked(tmp_path):
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../escaped.txt", "pwned")
    with pytest.raises(SafetyError, match="escapes the extraction directory"):
        extract_archive(str(archive), str(tmp_path / "out"), confine_to_home=False)
    assert not (tmp_path / "escaped.txt").exists()


def test_tar_symlink_member_is_blocked(tmp_path):
    archive = tmp_path / "evil.tar"
    link = tmp_path / "link"
    link.symlink_to("/etc/passwd")
    with tarfile.open(archive, "w") as bundle:
        bundle.add(link, arcname="link")
    with pytest.raises(SafetyError, match="is a link"):
        extract_archive(str(archive), str(tmp_path / "out"), confine_to_home=False)


def test_valid_zip_extracts(tmp_path):
    archive = tmp_path / "ok.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("inner/file.txt", "data")
    destination = tmp_path / "out"
    result = extract_archive(str(archive), str(destination), confine_to_home=False)
    assert result["members"] == 1
    assert (destination / "inner" / "file.txt").read_text() == "data"


def test_list_dir_reports_entries(tmp_path):
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "sub").mkdir()
    listing = list_dir(str(tmp_path), confine_to_home=False)
    names = {entry["name"]: entry["type"] for entry in listing["entries"]}
    assert names == {"a.txt": "file", "sub": "dir"}


def test_run_command_rejects_missing_binary():
    with pytest.raises(SafetyError, match="not installed"):
        run_command(["definitely-not-a-real-binary-xyz"])


def test_run_command_times_out():
    with pytest.raises(SafetyError, match="timed out"):
        run_command(["sleep", "5"], timeout=0.3)


def test_run_command_does_not_use_a_shell(tmp_path):
    marker = tmp_path / "pwned"
    result = run_command(["echo", f"hi; touch {marker}"])
    assert result["returncode"] == 0
    assert not marker.exists()
