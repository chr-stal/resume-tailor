"""Stage 1: ask Claude to suggest edits given a resume + job description.

Output: ``01-suggestions.json``::

    {
      "model": "claude-sonnet-4-6",
      "resume_path": "input/resume.md",
      "job_path": "input/job.txt",
      "suggestions": [
        {
          "id": "s1",
          "section": "Summary",
          "type": "replace",
          "original_text": "...",
          "suggested_text": "...",
          "rationale": "...",
          "priority": "high"
        },
        ...
      ]
    }

The suggestion schema is intentionally narrow:

* ``replace`` — find ``original_text`` in the resume, swap in ``suggested_text``.
* ``insert``  — insert ``suggested_text`` after the heading named in ``section``.
* ``delete``  — delete ``original_text``.

This keeps the apply step in :mod:`resume_tailor.apply` deterministic and
testable without any LLM in the loop.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from resume_tailor.state import (
    Workdir,
    read_text,
    write_json_atomic,
    write_text_atomic,
)


DEFAULT_MODEL = "claude-sonnet-4-6"


SYSTEM_PROMPT = """You are an experienced technical recruiter and resume editor.

You will be given a candidate's resume in Markdown and a job description in plain text. Your job is to propose specific, surgical edits to the resume that better tailor it to the job — without inventing experience the candidate doesn't have.

You MUST respond with a single JSON object (no prose, no code fences) of this exact shape:

{
  "suggestions": [
    {
      "id": "s1",
      "section": "Short heading-style label (e.g. 'Summary', 'Experience > Acme Corp')",
      "type": "replace" | "insert" | "delete",
      "original_text": "exact substring from the resume — required for replace/delete, empty string for insert",
      "suggested_text": "the new wording — required for replace/insert, empty string for delete",
      "rationale": "1-2 sentences on why this helps for THIS job",
      "priority": "high" | "medium" | "low"
    }
  ]
}

Hard rules:

1. ``original_text`` must be an EXACT substring of the resume Markdown — character-for-character, including punctuation and line breaks. If you cannot quote it exactly, do not include the suggestion.
2. Each ``original_text`` must be unique within the resume. If a phrase appears more than once, include enough surrounding text to make the match unambiguous.
3. Never invent skills, employers, dates, schools, metrics, or accomplishments. Only rewrite, reorder, or remove what's already there. If you want to add a missing keyword, do it inside an existing bullet using context the candidate clearly has.
4. Prefer fewer high-leverage edits over many small ones. 5–12 suggestions is a reasonable target.
5. IDs are ``s1``, ``s2``, ... in the order you list them.

Do not include anything outside the JSON object."""


USER_TEMPLATE = """=== JOB DESCRIPTION ===
{job}

=== RESUME (Markdown) ===
{resume}
"""


def _stage_inputs(wd: Workdir, resume_src: Path, job_src: Path) -> None:
    """Copy the user-supplied resume + JD into the workdir for reproducibility."""
    wd.ensure()
    if resume_src.resolve() != wd.resume_md.resolve():
        shutil.copyfile(resume_src, wd.resume_md)
    if job_src.resolve() != wd.job_txt.resolve():
        shutil.copyfile(job_src, wd.job_txt)


def _call_claude(resume_md: str, job_txt: str, model: str) -> dict[str, Any]:
    # Imported lazily so the rest of the CLI works without anthropic installed.
    from anthropic import Anthropic

    client = Anthropic()
    msg = client.messages.create(
        model=model,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": USER_TEMPLATE.format(job=job_txt, resume=resume_md),
            }
        ],
    )

    # Concatenate any text blocks (defensive — usually a single block).
    raw = "".join(block.text for block in msg.content if getattr(block, "type", None) == "text")
    return _parse_response(raw)


def _parse_response(raw: str) -> dict[str, Any]:
    """Parse Claude's response, tolerating accidental code fences."""
    text = raw.strip()
    if text.startswith("```"):
        # Strip the opening fence (with or without a language tag) and the closing fence.
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "Claude's response was not valid JSON. First 500 chars:\n" + raw[:500]
        ) from exc

    if not isinstance(data, dict) or "suggestions" not in data:
        raise RuntimeError("Claude's JSON response is missing a 'suggestions' key.")
    if not isinstance(data["suggestions"], list):
        raise RuntimeError("'suggestions' must be a list.")

    return data


def run_review(
    wd: Workdir,
    resume_src: Path,
    job_src: Path,
    *,
    force: bool = False,
    model: str | None = None,
) -> Path:
    """Run the review stage. Returns the path to the suggestions file."""
    if wd.suggestions_json.exists() and not force:
        print(
            f"[review] {wd.suggestions_json.name} already exists; skipping. "
            "Pass --force to regenerate.",
            file=sys.stderr,
        )
        return wd.suggestions_json

    if not resume_src.exists():
        raise FileNotFoundError(f"Resume not found: {resume_src}")
    if not job_src.exists():
        raise FileNotFoundError(f"Job description not found: {job_src}")

    _stage_inputs(wd, resume_src, job_src)

    resume_md = read_text(wd.resume_md)
    job_txt = read_text(wd.job_txt)

    chosen_model = model or os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL)
    print(f"[review] Calling Claude ({chosen_model})...", file=sys.stderr)
    response = _call_claude(resume_md, job_txt, chosen_model)

    payload = {
        "model": chosen_model,
        "resume_path": str(wd.resume_md.relative_to(wd.root)),
        "job_path": str(wd.job_txt.relative_to(wd.root)),
        "suggestions": response["suggestions"],
    }

    # Sanity-check that every original_text actually appears in the resume.
    bad = []
    for s in payload["suggestions"]:
        if s.get("type") in ("replace", "delete"):
            needle = s.get("original_text", "")
            if not needle or needle not in resume_md:
                bad.append(s.get("id", "?"))
    if bad:
        # Don't crash — just warn. The user can fix or skip them at approve time.
        print(
            f"[review] WARNING: original_text not found verbatim for: {', '.join(bad)}",
            file=sys.stderr,
        )

    write_json_atomic(wd.suggestions_json, payload)
    # Also dump the raw resume into the workdir if it wasn't there yet (already done above).
    print(
        f"[review] Wrote {len(payload['suggestions'])} suggestions to {wd.suggestions_json}",
        file=sys.stderr,
    )
    return wd.suggestions_json
