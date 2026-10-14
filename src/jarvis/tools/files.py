"""Filesystem tools with home confinement, overwrite and traversal protection."""

from __future__ import annotations

import shutil
import tarfile
import zipfile
from pathlib import Path
from typing import Any

from jarvis.permissions import PermissionLevel
from jarvis.safety import SafetyError, resolve_path
from jarvis.tools.base import Argument, Tool, ToolRegistry

TEXT_PREVIEW_BYTES = 8192


def list_dir(path: str = "~", limit: int = 100, confine_to_home: bool = True) -> dict[str, Any]:
    directory = resolve_path(path, confine_to_home=confine_to_home, must_exist=True)
    if not directory.is_dir():
        raise SafetyError(f"'{directory}' is not a directory")
    entries = []
    for entry in sorted(directory.iterdir(), key=lambda item: item.name)[:limit]:
        try:
            stat = entry.lstat()
        except OSError:
            continue
        entries.append(
            {
                "name": entry.name,
                "type": "dir" if entry.is_dir() else "symlink" if entry.is_symlink() else "file",
                "size": stat.st_size,
                "modified": stat.st_mtime,
            }
        )
    return {"path": str(directory), "entries": entries, "truncated": len(entries) >= limit}


def read_file(path: str, max_bytes: int = TEXT_PREVIEW_BYTES, confine_to_home: bool = True):
    target = resolve_path(path, confine_to_home=confine_to_home, must_exist=True)
    if not target.is_file():
        raise SafetyError(f"'{target}' is not a regular file")
    data = target.read_bytes()[:max_bytes]
    try:
        text = data.decode("utf-8")
        binary = False
    except UnicodeDecodeError:
        text = ""
        binary = True
    return {
        "path": str(target),
        "size": target.stat().st_size,
        "binary": binary,
        "content": text,
        "truncated": target.stat().st_size > max_bytes,
    }


def search_files(
    pattern: str, path: str = "~", limit: int = 50, confine_to_home: bool = True
) -> dict[str, Any]:
    root = resolve_path(path, confine_to_home=confine_to_home, must_exist=True)
    matches = []
    for candidate in root.rglob(pattern):
        matches.append(str(candidate))
        if len(matches) >= limit:
            break
    return {"root": str(root), "pattern": pattern, "matches": matches}


def largest_files(
    path: str = "~", limit: int = 10, confine_to_home: bool = True
) -> dict[str, Any]:
    root = resolve_path(path, confine_to_home=confine_to_home, must_exist=True)
    sized: list[tuple[int, str]] = []
    for candidate in root.rglob("*"):
        if candidate.is_symlink() or not candidate.is_file():
            continue
        try:
            sized.append((candidate.stat().st_size, str(candidate)))
        except OSError:
            continue
    sized.sort(reverse=True)
    return {
        "root": str(root),
        "files": [{"path": name, "size": size} for size, name in sized[:limit]],
    }


def write_file(
    path: str, content: str, overwrite: bool = False, confine_to_home: bool = True
) -> dict[str, Any]:
    target = resolve_path(path, confine_to_home=confine_to_home)
    if target.exists() and not overwrite:
        raise SafetyError(f"'{target}' already exists; pass overwrite=true to replace it")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {"path": str(target), "bytes_written": len(content.encode("utf-8"))}


def copy_path(
    source: str, destination: str, overwrite: bool = False, confine_to_home: bool = True
) -> dict[str, Any]:
    src = resolve_path(source, confine_to_home=confine_to_home, must_exist=True)
    dst = resolve_path(destination, confine_to_home=confine_to_home)
    if dst.exists() and not overwrite:
        raise SafetyError(f"'{dst}' already exists; pass overwrite=true to replace it")
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=overwrite)
    else:
        shutil.copy2(src, dst)
    return {"source": str(src), "destination": str(dst)}


def move_path(
    source: str, destination: str, overwrite: bool = False, confine_to_home: bool = True
) -> dict[str, Any]:
    src = resolve_path(source, confine_to_home=confine_to_home, must_exist=True)
    dst = resolve_path(destination, confine_to_home=confine_to_home)
    if dst.exists() and not overwrite:
        raise SafetyError(f"'{dst}' already exists; pass overwrite=true to replace it")
    shutil.move(str(src), str(dst))
    return {"source": str(src), "destination": str(dst)}


def delete_path(path: str, recursive: bool = False, confine_to_home: bool = True):
    target = resolve_path(path, confine_to_home=confine_to_home, must_exist=True)
    if target == Path.home().resolve():
        raise SafetyError("refusing to delete the home directory")
    if target.is_dir():
        if not recursive:
            raise SafetyError(f"'{target}' is a directory; pass recursive=true to delete it")
        shutil.rmtree(target)
    else:
        target.unlink()
    return {"deleted": str(target)}


def _assert_no_traversal(destination: Path, member_path: str) -> Path:
    resolved = (destination / member_path).resolve()
    if resolved != destination and destination not in resolved.parents:
        raise SafetyError(f"archive member '{member_path}' escapes the extraction directory")
    return resolved


def extract_archive(
    archive: str, destination: str, overwrite: bool = False, confine_to_home: bool = True
) -> dict[str, Any]:
    """Extract a ZIP or TAR archive, rejecting path traversal and symlink members."""
    src = resolve_path(archive, confine_to_home=confine_to_home, must_exist=True)
    dst = resolve_path(destination, confine_to_home=confine_to_home)
    if dst.exists() and any(dst.iterdir()) and not overwrite:
        raise SafetyError(f"'{dst}' is not empty; pass overwrite=true to extract into it")
    dst.mkdir(parents=True, exist_ok=True)
    dst = dst.resolve()

    if zipfile.is_zipfile(src):
        with zipfile.ZipFile(src) as bundle:
            names = bundle.namelist()
            for name in names:
                _assert_no_traversal(dst, name)
            bundle.extractall(dst)
        return {"archive": str(src), "destination": str(dst), "members": len(names)}

    if tarfile.is_tarfile(src):
        with tarfile.open(src) as bundle:
            members = bundle.getmembers()
            for member in members:
                if member.issym() or member.islnk():
                    raise SafetyError(f"archive member '{member.name}' is a link; refusing")
                _assert_no_traversal(dst, member.name)
            bundle.extractall(dst)
        return {"archive": str(src), "destination": str(dst), "members": len(members)}

    raise SafetyError(f"'{src}' is neither a ZIP nor a TAR archive")


def register(registry: ToolRegistry) -> None:
    confine = Argument("confine_to_home", bool, "Restrict paths to $HOME.", False, True)
    overwrite = Argument("overwrite", bool, "Allow replacing an existing path.", False, False)

    registry.register(
        Tool(
            "files.list",
            "List the entries of a directory.",
            "FILES",
            PermissionLevel.SAFE,
            list_dir,
            (
                Argument("path", str, "Directory to list.", False, "~"),
                Argument("limit", int, "Maximum entries to return.", False, 100),
                confine,
            ),
        )
    )
    registry.register(
        Tool(
            "files.read",
            "Read the beginning of a text file.",
            "FILES",
            PermissionLevel.SAFE,
            read_file,
            (
                Argument("path", str, "File to read."),
                Argument("max_bytes", int, "Maximum bytes to read.", False, TEXT_PREVIEW_BYTES),
                confine,
            ),
        )
    )
    registry.register(
        Tool(
            "files.search",
            "Find files matching a glob pattern below a directory.",
            "FILES",
            PermissionLevel.SAFE,
            search_files,
            (
                Argument("pattern", str, "Glob pattern, e.g. '**/*.py'."),
                Argument("path", str, "Root directory.", False, "~"),
                Argument("limit", int, "Maximum matches.", False, 50),
                confine,
            ),
            timeout=60.0,
        )
    )
    registry.register(
        Tool(
            "files.largest",
            "List the largest files below a directory.",
            "FILES",
            PermissionLevel.SAFE,
            largest_files,
            (
                Argument("path", str, "Root directory.", False, "~"),
                Argument("limit", int, "Number of files to return.", False, 10),
                confine,
            ),
            timeout=120.0,
        )
    )
    registry.register(
        Tool(
            "files.write",
            "Create or replace a text file.",
            "FILES",
            PermissionLevel.MODERATE,
            write_file,
            (
                Argument("path", str, "File to write."),
                Argument("content", str, "File content."),
                overwrite,
                confine,
            ),
        )
    )
    registry.register(
        Tool(
            "files.copy",
            "Copy a file or directory.",
            "FILES",
            PermissionLevel.MODERATE,
            copy_path,
            (
                Argument("source", str, "Source path."),
                Argument("destination", str, "Destination path."),
                overwrite,
                confine,
            ),
            timeout=120.0,
        )
    )
    registry.register(
        Tool(
            "files.move",
            "Move or rename a file or directory.",
            "FILES",
            PermissionLevel.HIGH,
            move_path,
            (
                Argument("source", str, "Source path."),
                Argument("destination", str, "Destination path."),
                overwrite,
                confine,
            ),
        )
    )
    registry.register(
        Tool(
            "files.delete",
            "Delete a file or directory.",
            "FILES",
            PermissionLevel.DESTRUCTIVE,
            delete_path,
            (
                Argument("path", str, "Path to delete."),
                Argument("recursive", bool, "Delete directories recursively.", False, False),
                confine,
            ),
        )
    )
    registry.register(
        Tool(
            "files.extract",
            "Extract a ZIP or TAR archive with traversal protection.",
            "FILES",
            PermissionLevel.HIGH,
            extract_archive,
            (
                Argument("archive", str, "Archive file."),
                Argument("destination", str, "Extraction directory."),
                overwrite,
                confine,
            ),
            timeout=120.0,
        )
    )
