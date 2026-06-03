"""Workdir conventions and atomic file I/O.

Every stage in the pipeline is just a function that reads files from a workdir
and writes files back into it. Centralising the path layout here means no stage
has to hardcode filenames, and changing the layout is a one-file edit.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Workdir layout
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Workdir:
    """A per-application directory holding everything for one resume tailoring run."""

    root: Path

    @property
    def input_dir(self) -> Path:
        return self.root / "input"

    @property
    def resume_md(self) -> Path:
        return self.input_dir / "resume.md"

    @property
    def job_txt(self) -> Path:
        return self.input_dir / "job.txt"

    @property
    def suggestions_json(self) -> Path:
        return self.root / "01-suggestions.json"

    @property
    def decisions_json(self) -> Path:
        return self.root / "02-decisions.json"

    @property
    def final_md(self) -> Path:
        return self.root / "03-final.md"

    @property
    def final_pdf(self) -> Path:
        return self.root / "04-resume.pdf"

    @property
    def final_html(self) -> Path:
        return self.root / "04-resume.html"

    @property
    def style_json(self) -> Path:
        return self.root / "style.json"

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.input_dir.mkdir(parents=True, exist_ok=True)


def open_workdir(path: str | os.PathLike) -> Workdir:
    wd = Workdir(Path(path).resolve())
    wd.ensure()
    return wd


# ---------------------------------------------------------------------------
# Atomic I/O
# ---------------------------------------------------------------------------

def write_json_atomic(path: Path, data: Any) -> None:
    """Write JSON to *path* via a temp file in the same dir, then rename.

    Atomic on POSIX. Prevents half-written JSON if the process dies mid-write,
    which matters because every stage trusts the previous stage's output file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        # Best-effort cleanup; original file is untouched.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")
