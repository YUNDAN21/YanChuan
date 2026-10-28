"""Explicit deletion of one owned meeting directory, independent of desktop applications."""

import os
import shutil
import sqlite3
from pathlib import Path


def _reject_link(path):
    if path.is_symlink() or path.is_junction():
        raise ValueError("会议记录包含链接目录或文件，无法删除")


def meeting_for_deletion(root: Path, directory: Path):
    """Validate the complete target before a caller releases connections or removes files."""
    root = Path(root).resolve()
    candidate = Path(directory).absolute()
    _reject_link(candidate)
    directory = candidate.resolve()
    if directory.parent != root or directory == root:
        raise ValueError("只能删除会议记录目录中的单个会议")
    if not directory.is_dir() or not (directory / "meeting.sqlite3").is_file():
        raise ValueError("会议记录不存在")
    for current, folders, files in os.walk(directory, followlinks=False):
        for name in [*folders, *files]:
            _reject_link(Path(current) / name)
    database = sqlite3.connect((directory / "meeting.sqlite3").as_uri() + "?mode=ro", uri=True)
    try:
        row = database.execute("SELECT id,title FROM meeting").fetchone()
        if row is None:
            raise ValueError("会议记录无效")
        return {"id": row[0], "title": row[1], "directory": str(directory)}
    finally:
        database.close()


def delete_meeting(root: Path, directory: Path):
    """Permanently remove that meeting's database, conversation and exports.

    Callers must finish recording/summary work and release all connections first.
    Paths outside the explicit root, its root itself, and reparse points are rejected.
    """
    metadata = meeting_for_deletion(root, directory)
    shutil.rmtree(Path(metadata["directory"]))
    return metadata
