"""Stage 2: walk through suggestions one at a time, recording decisions.

Output: ``02-decisions.json``::

    {
      "decisions": [
        {"id": "s1", "action": "approve", "final_text": "..."},
        {"id": "s2", "action": "deny",    "final_text": ""},
        {"id": "s3", "action": "edit",    "final_text": "user-edited replacement"}
      ]
    }

Saved after every single decision, so a Ctrl-C never costs more than the one
suggestion in front of you. The next run automatically resumes at the first
suggestion that doesn't yet have a decision.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from typing import Any

from resume_tailor.state import Workdir, read_json, write_json_atomic


VALID_ACTIONS = {"approve", "deny", "edit"}


def _existing_decisions(wd: Workdir) -> dict[str, dict[str, Any]]:
    if not wd.decisions_json.exists():
        return {}
    data = read_json(wd.decisions_json)
    return {d["id"]: d for d in data.get("decisions", [])}


def _save_decisions(wd: Workdir, decisions_by_id: dict[str, dict[str, Any]], order: list[str]) -> None:
    payload = {
        "decisions": [decisions_by_id[i] for i in order if i in decisions_by_id],
    }
    write_json_atomic(wd.decisions_json, payload)


def _editor_prompt(initial: str) -> str:
    """Open $EDITOR (or `nano`) on a temp file pre-populated with *initial*.

    Used when the user picks 'edit' on a suggestion. Returns the saved contents.
    """
    editor = os.environ.get("EDITOR", "").strip() or _default_editor()
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8"
    ) as f:
        f.write(initial)
        f.write(
            "\n\n# --- Edit above. Lines starting with '#' will NOT be stripped. "
            "Save and exit when done. ---\n"
        )
        path = f.name
    try:
        subprocess.run([editor, path], check=False)
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    # Drop the trailing instructions line we added.
    text = text.split(
        "\n\n# --- Edit above. Lines starting with '#' will NOT be stripped. "
    )[0]
    return text.rstrip() + "\n" if text.strip() else ""


def _default_editor() -> str:
    for candidate in ("nano", "vim", "vi", "notepad"):
        from shutil import which
        if which(candidate):
            return candidate
    return "vi"


def _print_suggestion(idx: int, total: int, s: dict[str, Any]) -> None:
    bar = "─" * 70
    print()
    print(bar)
    print(f"[{idx}/{total}] {s['id']}  ·  {s.get('section', '')}  ·  {s.get('priority', '')}")
    print(f"type: {s.get('type', '')}")
    print(bar)
    if s.get("type") in ("replace", "delete") and s.get("original_text"):
        print("ORIGINAL:")
        print(_indent(s["original_text"]))
        print()
    if s.get("type") in ("replace", "insert") and s.get("suggested_text"):
        label = "INSERT" if s["type"] == "insert" else "SUGGESTED"
        print(f"{label}:")
        print(_indent(s["suggested_text"]))
        print()
    if s.get("rationale"):
        print(f"why: {s['rationale']}")


def _indent(text: str, prefix: str = "  | ") -> str:
    return "\n".join(prefix + line for line in text.splitlines())


def _ask(prompt: str, valid: set[str]) -> str:
    while True:
        try:
            ans = input(prompt).strip().lower()
        except EOFError:
            return "q"
        if ans in valid:
            return ans
        print(f"  please enter one of: {', '.join(sorted(valid))}")


def run_approve(wd: Workdir, *, restart: bool = False) -> None:
    if not wd.suggestions_json.exists():
        raise FileNotFoundError(
            f"Suggestions file not found: {wd.suggestions_json}\n"
            f"Run `review` first, or drop a hand-written suggestions file there."
        )

    suggestions = read_json(wd.suggestions_json)["suggestions"]
    if not suggestions:
        print("[approve] No suggestions to review. Writing empty decisions file.")
        write_json_atomic(wd.decisions_json, {"decisions": []})
        return

    order = [s["id"] for s in suggestions]

    if restart:
        decisions_by_id: dict[str, dict[str, Any]] = {}
    else:
        decisions_by_id = _existing_decisions(wd)

    # Resume at the first suggestion that doesn't yet have a decision.
    start = 0
    for i, s in enumerate(suggestions):
        if s["id"] not in decisions_by_id:
            start = i
            break
    else:
        start = len(suggestions)  # all already decided

    if start > 0:
        print(
            f"[approve] Resuming at suggestion {start + 1}/{len(suggestions)} "
            f"({len(decisions_by_id)} already decided). "
            f"Pass --restart to start over.",
            file=sys.stderr,
        )

    valid_choices = {"a", "d", "e", "s", "q"}

    for i in range(start, len(suggestions)):
        s = suggestions[i]
        _print_suggestion(i + 1, len(suggestions), s)
        choice = _ask(
            "[a]pprove  [d]eny  [e]dit  [s]kip  [q]uit&save: ",
            valid_choices,
        )

        if choice == "q":
            print("[approve] Quitting. Progress saved.", file=sys.stderr)
            _save_decisions(wd, decisions_by_id, order)
            return
        if choice == "s":
            # Don't record a decision — next run will resume here.
            continue

        action_map = {"a": "approve", "d": "deny", "e": "edit"}
        action = action_map[choice]

        if action == "approve":
            final_text = s.get("suggested_text", "") if s.get("type") != "delete" else ""
        elif action == "deny":
            final_text = ""
        else:  # edit
            seed = s.get("suggested_text", "") if s.get("type") != "delete" else s.get("original_text", "")
            final_text = _editor_prompt(seed)
            if not final_text:
                print("  (empty after edit; treating as deny)")
                action = "deny"

        decisions_by_id[s["id"]] = {
            "id": s["id"],
            "action": action,
            "final_text": final_text,
        }
        # Save after every decision so a crash never costs more than one.
        _save_decisions(wd, decisions_by_id, order)

    _save_decisions(wd, decisions_by_id, order)
    print(
        f"[approve] Done. {len(decisions_by_id)} decisions written to {wd.decisions_json}",
        file=sys.stderr,
    )
