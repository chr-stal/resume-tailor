"""Stage 3: deterministically apply approved decisions to produce the final markdown.

Inputs:
  * ``input/resume.md``   — the original resume
  * ``01-suggestions.json`` — for each decision, look up the original suggestion
    so we know its ``type``, ``original_text``, and (for inserts) the ``section``
    heading to insert under.
  * ``02-decisions.json`` — your approve/deny/edit calls.

Output: ``03-final.md``.

This stage does not call any LLM. You can re-run it after hand-editing
``02-decisions.json`` and you'll get a perfectly reproducible result.
"""

from __future__ import annotations

import re
import sys
from typing import Any

from resume_tailor.state import Workdir, read_json, read_text, write_text_atomic


def _index_suggestions(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {s["id"]: s for s in payload.get("suggestions", [])}


def _apply_replace(text: str, original: str, replacement: str) -> tuple[str, bool]:
    """Replace the first occurrence of *original* with *replacement*."""
    idx = text.find(original)
    if idx == -1:
        return text, False
    return text[:idx] + replacement + text[idx + len(original):], True


def _apply_delete(text: str, original: str) -> tuple[str, bool]:
    """Delete the first occurrence of *original*. Trims one trailing newline if present."""
    idx = text.find(original)
    if idx == -1:
        return text, False
    end = idx + len(original)
    # If the deleted block is followed by a single newline, gobble it so we don't
    # leave a dangling blank line.
    if end < len(text) and text[end] == "\n":
        end += 1
    return text[:idx] + text[end:], True


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


def _apply_insert(text: str, section: str, content: str) -> tuple[str, bool]:
    """Insert *content* immediately after the heading whose text matches *section*.

    Matching is case-insensitive and tolerates extra whitespace. The inserted
    content is placed on its own block with a leading and trailing blank line.
    """
    if not section:
        # No anchor — append at end.
        return text.rstrip() + "\n\n" + content.rstrip() + "\n", True

    target = section.strip().lower()
    # Allow "Experience > Acme Corp" — match on the last segment as the heading text.
    target_tail = target.split(">")[-1].strip()

    # Try in order: exact match → tail exact match → tail substring match.
    headings = list(_HEADING_RE.finditer(text))
    chosen_idx: int | None = None
    chosen_priority = 99
    for i, m in enumerate(headings):
        heading_text = m.group(2).strip().lower()
        if heading_text == target:
            priority = 0
        elif heading_text == target_tail:
            priority = 1
        elif target_tail and target_tail in heading_text:
            priority = 2
        else:
            continue
        if priority < chosen_priority:
            chosen_priority = priority
            chosen_idx = i

    if chosen_idx is not None:
        m = headings[chosen_idx]
        section_level = len(m.group(1))
        # Find the end of this section: the next heading at the same or higher level.
        section_end = len(text)
        for nxt in headings[chosen_idx + 1:]:
            if len(nxt.group(1)) <= section_level:
                section_end = nxt.start()
                break
        # Trim trailing whitespace/newlines so the insert sits right after the
        # section's last non-empty line.
        end_trimmed = section_end
        while end_trimmed > 0 and text[end_trimmed - 1] in "\n\r\t ":
            end_trimmed -= 1
        block = "\n" + content.rstrip() + "\n"
        return text[:end_trimmed] + block + text[end_trimmed:], True

    # Fallback: append at end with a labelled note.
    return (
        text.rstrip()
        + f"\n\n<!-- inserted (no heading matched '{section}') -->\n"
        + content.rstrip()
        + "\n",
        True,
    )


def run_apply(wd: Workdir) -> None:
    if not wd.resume_md.exists():
        raise FileNotFoundError(
            f"Original resume not found at {wd.resume_md}. "
            "Run `review` first or copy your resume.md into input/."
        )

    decisions_payload = (
        read_json(wd.decisions_json) if wd.decisions_json.exists() else {"decisions": []}
    )
    decisions = decisions_payload.get("decisions", [])

    suggestions_by_id: dict[str, dict[str, Any]] = {}
    if wd.suggestions_json.exists():
        suggestions_by_id = _index_suggestions(read_json(wd.suggestions_json))

    text = read_text(wd.resume_md)
    applied = 0
    skipped: list[str] = []

    for d in decisions:
        if d.get("action") != "approve" and d.get("action") != "edit":
            continue

        sid = d.get("id")
        sug = suggestions_by_id.get(sid)
        if sug is None:
            skipped.append(f"{sid} (no matching suggestion)")
            continue

        kind = sug.get("type")
        final_text = d.get("final_text", "")

        if kind == "replace":
            text, ok = _apply_replace(text, sug.get("original_text", ""), final_text)
        elif kind == "delete":
            text, ok = _apply_delete(text, sug.get("original_text", ""))
        elif kind == "insert":
            text, ok = _apply_insert(text, sug.get("section", ""), final_text)
        else:
            skipped.append(f"{sid} (unknown type {kind!r})")
            continue

        if ok:
            applied += 1
        else:
            skipped.append(f"{sid} (original_text not found)")

    write_text_atomic(wd.final_md, text)

    print(
        f"[apply] Applied {applied} decision(s); wrote {wd.final_md}",
        file=sys.stderr,
    )
    if skipped:
        print(
            f"[apply] Skipped {len(skipped)}: {', '.join(skipped)}",
            file=sys.stderr,
        )
