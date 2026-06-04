"""Flask UI on top of the resume-tailor pipeline.

The four pipeline stages (review / apply / build) are exposed as POST endpoints
that simply delegate to the existing CLI functions. The interactive approve
step from the CLI is replaced by a per-decision save endpoint backing a card
UI in the browser. Same files on disk, same fault-tolerance story — just a
nicer way to drive it.
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
import tempfile
from functools import wraps
from pathlib import Path

from flask import (
    Flask,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)

from resume_tailor.apply import run_apply
from resume_tailor.build import (
    DEFAULT_STYLE,
    PRESETS,
    load_style,
    run_build,
)
from resume_tailor.review import run_review
from resume_tailor.state import (
    Workdir,
    open_workdir,
    read_json,
    read_text,
    write_json_atomic,
    write_text_atomic,
)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# resume_tailor/web/app.py — go up two levels to reach the project root.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNS_ROOT = PROJECT_ROOT / "runs"
DEMO_RUNS_SRC = PROJECT_ROOT / "demo-runs"

# Where session-scoped demo runs live. Lives in /tmp so it clears naturally on
# container restart and never pollutes the project tree on local dev.
DEMO_SESSIONS_ROOT = Path(tempfile.gettempdir()) / "resume-tailor-demo-sessions"

# Names of baseline demo workdirs that are intentionally read-only — visitors
# can browse them but can't mutate. (They're the showcase of "what the tool
# produces" at different points.) Anything created by a visitor lives under
# DEMO_SESSIONS_ROOT/<sid>/ and is freely mutable.
DEMO_BASELINE_RUNS = {
    "acme-staff-eng-completed",
    "stripe-payments-in-progress",
    "google-cloud-clickrun",
}

# Slugs for run names. Lowercase, no spaces, no path traversal.
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


# ---------------------------------------------------------------------------
# Demo mode
# ---------------------------------------------------------------------------

# Workdir name → pre-staged suggestions file. When a user clicks "Run review"
# on that workdir while DEMO_MODE is on, we copy the staged file into place
# instead of calling Claude. Lets the "click → suggestions appear" moment
# happen on a public demo without spending real API credits.
DEMO_CLICKRUN_RUNS = {
    "google-cloud-clickrun": "_demo-suggestions.json",
}


def _demo_sid() -> str:
    """Return (or lazily mint) a session-scoped id for the current visitor.

    Stored in the Flask signed session cookie. The id keys a per-visitor
    directory under :data:`DEMO_SESSIONS_ROOT` where any new runs and
    decisions land — keeping each visitor's experience isolated.
    """
    sid = session.get("demo_sid")
    if not sid:
        sid = secrets.token_urlsafe(8)
        session["demo_sid"] = sid
    return sid


def _demo_session_root() -> Path:
    """Per-visitor runs directory. Created on demand."""
    root = DEMO_SESSIONS_ROOT / _demo_sid()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _is_session_workdir(wd: "Workdir") -> bool:
    """True iff *wd* lives under the current visitor's demo-session dir."""
    try:
        wd.root.relative_to(DEMO_SESSIONS_ROOT)
        return True
    except ValueError:
        return False


def _demo_fork_if_baseline(wd: "Workdir", name: str) -> "Workdir":
    """In demo mode, fork a baseline workdir to the visitor's session dir on
    first mutation.

    Reads always check the session dir first (see :func:`_wd_or_404`), so once
    the fork exists every subsequent request — read or write — flows to the
    session copy. The baseline on disk stays untouched and other visitors keep
    seeing it as the canonical sample.
    """
    if not current_app.config.get("DEMO_MODE"):
        return wd
    if _is_session_workdir(wd):
        return wd
    session_root = _demo_session_root()
    session_dir = session_root / name
    if not session_dir.exists():
        shutil.copytree(wd.root, session_dir)
    return open_workdir(session_dir)


def _seed_demo_runs(app: Flask) -> None:
    """On first boot in demo mode, copy committed demo workdirs into runs/.

    Idempotent: if a destination already exists we leave it alone. To force a
    fresh state, delete the matching directory under ``runs/`` and restart.
    """
    if not app.config.get("DEMO_MODE"):
        return
    if not DEMO_RUNS_SRC.exists():
        app.logger.info("demo mode: no demo-runs/ source directory; skipping seed")
        return
    RUNS_ROOT.mkdir(parents=True, exist_ok=True)
    for src in DEMO_RUNS_SRC.iterdir():
        if not src.is_dir():
            continue
        dst = RUNS_ROOT / src.name
        if dst.exists():
            continue
        shutil.copytree(src, dst)
        app.logger.info("demo mode: seeded %s", dst)


def demo_blocked(action_label: str = "this action"):
    """Decorator: in demo mode, swallow the call and flash a friendly message.

    Apply to POST handlers that would mutate disk in ways the demo shouldn't
    persist. JSON endpoints get a 200 + {"ok": true, "demo": true} so the
    front-end can still light up; redirect endpoints bounce back where they
    came from.
    """
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if current_app.config.get("DEMO_MODE"):
                if request.is_json or request.headers.get("Accept", "").startswith("application/json"):
                    return jsonify({"ok": True, "demo": True}), 200
                flash(f"Demo mode — {action_label} isn't persisted. Clone the repo to run for real.", "info")
                return redirect(request.referrer or url_for("index"))
            return view(*args, **kwargs)
        return wrapped
    return decorator


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = os.environ.get("RESUME_TAILOR_SECRET", "dev-resume-tailor")
    app.config["DEMO_MODE"] = os.environ.get("RESUME_TAILOR_DEMO", "0") in ("1", "true", "True")

    # Pull ANTHROPIC_API_KEY from .env if present.
    try:
        from dotenv import load_dotenv
        load_dotenv(PROJECT_ROOT / ".env")
    except ImportError:
        pass

    RUNS_ROOT.mkdir(parents=True, exist_ok=True)
    _seed_demo_runs(app)

    @app.context_processor
    def _inject_demo_flag():
        return {"demo_mode": app.config.get("DEMO_MODE", False)}

    # ----- helpers -----
    def _wd_or_404(name: str) -> Workdir:
        if not SLUG_RE.match(name):
            abort(404)
        # In demo mode, a visitor's session-scoped copy wins over the baseline.
        if app.config.get("DEMO_MODE"):
            sd = DEMO_SESSIONS_ROOT / _demo_sid() / name
            if sd.exists() and sd.is_dir():
                return open_workdir(sd)
        run_dir = RUNS_ROOT / name
        if not run_dir.exists() or not run_dir.is_dir():
            abort(404)
        return open_workdir(run_dir)

    def _run_info(p: Path) -> dict:
        wd = open_workdir(p)
        return {
            "name": p.name,
            "has_resume": wd.resume_md.exists(),
            "has_jd": wd.job_txt.exists(),
            "has_input": wd.resume_md.exists() and wd.job_txt.exists(),
            "has_suggestions": wd.suggestions_json.exists(),
            "has_decisions": wd.decisions_json.exists(),
            "has_final_md": wd.final_md.exists(),
            "has_pdf": wd.final_pdf.exists(),
            "has_html": wd.final_html.exists(),
        }

    def _run_card(p: Path) -> dict:
        """Richer per-run summary for the index card grid.

        Pulls a human-readable title from the JD, computes a single 'status'
        string the card can render, and surfaces counts the user actually
        cares about at-a-glance.
        """
        wd = open_workdir(p)
        info = _run_info(p)

        # Title: first non-empty, non-boilerplate line of the JD, capped.
        title = ""
        if wd.job_txt.exists():
            for raw in read_text(wd.job_txt).splitlines():
                line = raw.strip().lstrip("#").strip()
                if not line:
                    continue
                if line.lower() in {"job description", "jd", "role"}:
                    continue
                title = line
                break
        title = (title[:80] + "…") if len(title) > 80 else title

        # Counts.
        suggestion_count = 0
        if wd.suggestions_json.exists():
            try:
                suggestion_count = len(read_json(wd.suggestions_json).get("suggestions", []))
            except Exception:
                suggestion_count = 0

        decisions_total = 0
        approved = denied = edited = 0
        if wd.decisions_json.exists():
            try:
                decisions = read_json(wd.decisions_json).get("decisions", [])
                decisions_total = len(decisions)
                for d in decisions:
                    a = d.get("action")
                    if a == "approve": approved += 1
                    elif a == "deny": denied += 1
                    elif a == "edit": edited += 1
            except Exception:
                pass

        # Status — single short label.
        is_markdown_only = (
            info["has_final_md"] and not info["has_suggestions"]
        )
        if info["has_pdf"]:
            status, status_class = "done", "done"
        elif info["has_final_md"]:
            status, status_class = "ready to build", "ready-build"
        elif not info["has_suggestions"]:
            if is_markdown_only:
                status, status_class = "markdown only", "markdown"
            else:
                status, status_class = "needs review", "needs-review"
        elif decisions_total == 0:
            status, status_class = "needs approval", "needs-approval"
        elif decisions_total < suggestion_count:
            status, status_class = f"approving {decisions_total}/{suggestion_count}", "approving"
        else:
            status, status_class = "ready to apply", "ready-apply"

        # "Last touched" — newest mtime in the workdir, formatted relatively.
        latest = p.stat().st_mtime
        for child in p.rglob("*"):
            try:
                latest = max(latest, child.stat().st_mtime)
            except OSError:
                pass

        return {
            **info,
            "title": title or "(no job description)",
            "suggestion_count": suggestion_count,
            "decisions_total": decisions_total,
            "approved": approved,
            "denied": denied,
            "edited": edited,
            "status": status,
            "status_class": status_class,
            "is_done": status_class == "done",
            "is_in_progress": status_class not in ("done", "markdown"),
            "is_markdown_only": is_markdown_only,
            "mtime": latest,
            "mtime_label": _relative_time(latest),
        }

    def _relative_time(ts: float) -> str:
        import time
        delta = time.time() - ts
        if delta < 60:
            return "just now"
        if delta < 3600:
            return f"{int(delta // 60)} min ago"
        if delta < 86400:
            return f"{int(delta // 3600)} hr ago"
        days = int(delta // 86400)
        if days == 1:
            return "yesterday"
        if days < 30:
            return f"{days} days ago"
        if days < 365:
            return f"{days // 30} mo ago"
        return f"{days // 365} yr ago"

    def _decision_summary(decisions_by_id: dict) -> dict:
        return {
            "approved": sum(1 for d in decisions_by_id.values() if d.get("action") == "approve"),
            "denied":   sum(1 for d in decisions_by_id.values() if d.get("action") == "deny"),
            "edited":   sum(1 for d in decisions_by_id.values() if d.get("action") == "edit"),
            "total":    len(decisions_by_id),
        }

    def _load_decisions_by_id(wd: Workdir) -> dict:
        if not wd.decisions_json.exists():
            return {}
        return {d["id"]: d for d in read_json(wd.decisions_json).get("decisions", [])}

    def _load_meta(wd: Workdir) -> dict:
        """Per-run metadata (currently just the PDF download filename).

        Missing keys fall back to defaults computed from the run name, so a run
        without a meta.json behaves identically to one with the defaults
        written out.
        """
        meta = {"pdf_filename": f"{wd.root.name}-resume.pdf"}
        if wd.meta_json.exists():
            try:
                stored = read_json(wd.meta_json) or {}
                if isinstance(stored, dict):
                    for k, v in stored.items():
                        if isinstance(v, str) and v.strip():
                            meta[k] = v.strip()
            except Exception:
                pass
        return meta

    def _safe_pdf_filename(raw: str, default: str) -> str:
        """Sanitize a user-supplied filename. Strips path components, enforces .pdf."""
        import os.path
        name = os.path.basename(raw or "").strip()
        if not name:
            return default
        # Replace anything outside a conservative allowlist with '-'.
        cleaned = "".join(c if c.isalnum() or c in "._- " else "-" for c in name).strip(" .-_")
        if not cleaned:
            return default
        if not cleaned.lower().endswith(".pdf"):
            cleaned += ".pdf"
        return cleaned[:120]

    def _save_decisions(wd: Workdir, decisions_by_id: dict, order: list[str]) -> None:
        payload = {"decisions": [decisions_by_id[i] for i in order if i in decisions_by_id]}
        write_json_atomic(wd.decisions_json, payload)

    # ----- routes -----
    @app.route("/")
    def index():
        runs = []
        for p in sorted(RUNS_ROOT.iterdir()) if RUNS_ROOT.exists() else []:
            if p.is_dir() and not p.name.startswith("."):
                card = _run_card(p)
                card["is_session"] = False
                runs.append(card)

        # Demo mode: also surface the visitor's own session-scoped runs so they
        # see what they created on this visit, separated visually from the
        # read-only baseline samples.
        if app.config.get("DEMO_MODE"):
            session_root = DEMO_SESSIONS_ROOT / _demo_sid()
            if session_root.exists():
                for p in sorted(session_root.iterdir()):
                    if p.is_dir() and not p.name.startswith("."):
                        card = _run_card(p)
                        card["is_session"] = True
                        runs.append(card)

        # Default sort: most recently touched first.
        runs.sort(key=lambda r: r["mtime"], reverse=True)

        counts = {
            "all":         len(runs),
            "in_progress": sum(1 for r in runs if r["is_in_progress"]),
            "done":        sum(1 for r in runs if r["is_done"]),
            "markdown":    sum(1 for r in runs if r["is_markdown_only"]),
        }
        return render_template("index.html", runs=runs, counts=counts)

    @app.route("/runs/new")
    def new_run_form():
        """Standalone page for the LLM-driven create flow."""
        defaults = _demo_autofill_defaults() if app.config.get("DEMO_MODE") else {}
        return render_template("new_run.html", defaults=defaults)

    @app.route("/runs/new-markdown")
    def new_markdown_form():
        """Standalone page for the Markdown-only create flow."""
        defaults = _demo_autofill_defaults() if app.config.get("DEMO_MODE") else {}
        return render_template("new_markdown.html", defaults=defaults)

    def _demo_autofill_defaults() -> dict:
        """Pull resume + JD content from one of the baseline demo workdirs to
        pre-populate the new-run form, and mint a unique-ish run name so two
        visitors don't collide on the default."""
        sample_dir = DEMO_RUNS_SRC / "google-cloud-clickrun"
        resume_md = ""
        job_txt = ""
        try:
            resume_md = (sample_dir / "input" / "resume.md").read_text(encoding="utf-8")
            job_txt = (sample_dir / "input" / "job.txt").read_text(encoding="utf-8")
        except FileNotFoundError:
            pass
        suffix = secrets.token_urlsafe(4).lower().replace("_", "-").replace("=", "")
        suffix = re.sub(r"[^a-z0-9-]", "-", suffix)[:6] or "demo"
        return {
            "name": f"try-it-now-{suffix}",
            "resume": resume_md,
            "jd": job_txt,
            "markdown": resume_md,
        }

    @app.route("/runs", methods=["POST"])
    def create_run():
        name = (request.form.get("name") or "").strip().lower()
        resume = request.form.get("resume") or ""
        jd = request.form.get("jd") or ""
        if not SLUG_RE.match(name):
            flash("Run name must be lowercase letters, numbers, hyphens, or underscores.", "error")
            return redirect(url_for("index"))
        if not resume.strip() or not jd.strip():
            flash("Both resume and job description are required.", "error")
            return redirect(url_for("index"))

        if app.config.get("DEMO_MODE"):
            # New runs in demo mode live under the visitor's session dir.
            session_root = _demo_session_root()
            if name in DEMO_BASELINE_RUNS:
                flash(f"'{name}' is reserved for a baseline demo run. Pick another name.", "error")
                return redirect(url_for("new_run_form"))
            run_dir = session_root / name
        else:
            run_dir = RUNS_ROOT / name

        if run_dir.exists():
            flash(f"Run '{name}' already exists.", "error")
            return redirect(url_for("index"))

        wd = open_workdir(run_dir)
        write_text_atomic(wd.resume_md, resume.rstrip() + "\n")
        write_text_atomic(wd.job_txt, jd.rstrip() + "\n")

        # Pre-stage mock suggestions in demo mode so that 'Run review' has
        # something to load (the model never gets called).
        if app.config.get("DEMO_MODE"):
            mock = DEMO_RUNS_SRC / "google-cloud-clickrun" / "_demo-suggestions.json"
            if mock.exists():
                (wd.root / "_demo-suggestions.json").write_bytes(mock.read_bytes())

        flash(f"Created run '{name}'.", "success")
        return redirect(url_for("run_view", name=name))

    @app.route("/runs/markdown", methods=["POST"])
    def create_markdown_run():
        """Create a run with just hand-written Markdown — no Claude review.

        We write the same text into both ``input/resume.md`` (so the workdir is
        well-formed) and ``03-final.md`` (so the build step has something to
        render immediately). The user lands on the Markdown editor and can
        tune/rebuild from there.
        """
        name = (request.form.get("name") or "").strip().lower()
        markdown = request.form.get("markdown") or ""
        if not SLUG_RE.match(name):
            flash("Run name must be lowercase letters, numbers, hyphens, or underscores.", "error")
            return redirect(url_for("index"))
        if not markdown.strip():
            flash("Markdown content is required.", "error")
            return redirect(url_for("index"))

        if app.config.get("DEMO_MODE"):
            if name in DEMO_BASELINE_RUNS:
                flash(f"'{name}' is reserved for a baseline demo run. Pick another name.", "error")
                return redirect(url_for("new_markdown_form"))
            run_dir = _demo_session_root() / name
        else:
            run_dir = RUNS_ROOT / name

        if run_dir.exists():
            flash(f"Run '{name}' already exists.", "error")
            return redirect(url_for("index"))

        wd = open_workdir(run_dir)
        text = markdown.rstrip() + "\n"
        write_text_atomic(wd.resume_md, text)
        write_text_atomic(wd.final_md, text)
        flash(f"Created Markdown-only run '{name}'.", "success")
        return redirect(url_for("markdown_view", name=name))

    @app.route("/runs/<name>")
    def run_view(name):
        wd = _wd_or_404(name)
        info = _run_info(wd.root)
        suggestion_count = 0
        decision_summary = None
        if wd.suggestions_json.exists():
            suggestion_count = len(read_json(wd.suggestions_json).get("suggestions", []))
        if wd.decisions_json.exists():
            decisions_by_id = _load_decisions_by_id(wd)
            decision_summary = _decision_summary(decisions_by_id)
        meta = _load_meta(wd)
        return render_template(
            "run.html",
            name=name,
            info=info,
            suggestion_count=suggestion_count,
            decision_summary=decision_summary,
            meta=meta,
        )

    @app.route("/runs/<name>/review", methods=["POST"])
    def run_review_route(name):
        wd = _wd_or_404(name)

        if app.config.get("DEMO_MODE"):
            # In demo mode the model is never called. Both the baseline
            # click-to-run workdir and visitor-created session runs have a
            # _demo-suggestions.json sitting beside them — we just copy it in
            # to simulate "the review just ran."
            staged = wd.root / "_demo-suggestions.json"
            if not staged.exists():
                flash(
                    "Demo mode — review isn't run for this read-only sample. "
                    "Create your own run via '+ Tailor for a job' to try it.",
                    "info",
                )
                return redirect(url_for("run_view", name=name))
            wd.suggestions_json.write_bytes(staged.read_bytes())
            flash("Demo: mock suggestions loaded (no API call was made).", "success")
            return redirect(url_for("run_view", name=name))

        force = request.form.get("force") == "1"
        try:
            run_review(wd, wd.resume_md, wd.job_txt, force=force)
            flash("Review complete.", "success")
        except Exception as e:
            flash(f"Review failed: {e}", "error")
        return redirect(url_for("run_view", name=name))

    @app.route("/runs/<name>/apply", methods=["POST"])
    def run_apply_route(name):
        wd = _wd_or_404(name)
        wd = _demo_fork_if_baseline(wd, name)
        nxt = request.args.get("next") or request.form.get("next")
        try:
            run_apply(wd)
            flash("Applied decisions.", "success")
        except Exception as e:
            flash(f"Apply failed: {e}", "error")
            return redirect(url_for("run_view", name=name))
        if nxt == "editor":
            return redirect(url_for("markdown_view", name=name))
        return redirect(url_for("run_view", name=name))

    @app.route("/runs/<name>/build", methods=["POST"])
    def run_build_route(name):
        wd = _wd_or_404(name)
        wd = _demo_fork_if_baseline(wd, name)
        html_only = request.form.get("html_only") == "1"
        try:
            run_build(wd, html_only=html_only)
            flash("Built output.", "success")
        except Exception as e:
            flash(f"Build failed: {e}", "error")
        return redirect(url_for("run_view", name=name))

    @app.route("/runs/<name>/approve")
    def approve_view(name):
        wd = _wd_or_404(name)
        if not wd.suggestions_json.exists():
            flash("No suggestions yet — run review first.", "error")
            return redirect(url_for("run_view", name=name))
        suggestions = read_json(wd.suggestions_json).get("suggestions", [])
        decisions_by_id = _load_decisions_by_id(wd)
        jd = read_text(wd.job_txt) if wd.job_txt.exists() else ""
        return render_template(
            "approve.html",
            name=name,
            suggestions=suggestions,
            decisions=decisions_by_id,
            jd=jd,
            summary=_decision_summary(decisions_by_id),
        )

    @app.route("/runs/<name>/decisions/<sid>", methods=["POST"])
    def save_decision(name, sid):
        wd = _wd_or_404(name)
        wd = _demo_fork_if_baseline(wd, name)
        body = request.get_json(silent=True) or {}
        action = body.get("action")
        final_text = body.get("final_text", "")
        if action not in ("approve", "deny", "edit"):
            return jsonify({"error": "invalid action"}), 400
        if not wd.suggestions_json.exists():
            return jsonify({"error": "no suggestions"}), 400
        suggestions = read_json(wd.suggestions_json).get("suggestions", [])
        order = [s["id"] for s in suggestions]
        if sid not in order:
            return jsonify({"error": "unknown suggestion id"}), 404

        decisions_by_id = _load_decisions_by_id(wd)
        decisions_by_id[sid] = {"id": sid, "action": action, "final_text": final_text}
        _save_decisions(wd, decisions_by_id, order)
        return jsonify({"ok": True, "summary": _decision_summary(decisions_by_id)})

    @app.route("/runs/<name>/decisions/<sid>", methods=["DELETE"])
    def clear_decision(name, sid):
        wd = _wd_or_404(name)
        wd = _demo_fork_if_baseline(wd, name)
        if not wd.suggestions_json.exists():
            return jsonify({"ok": True, "summary": _decision_summary({})})
        suggestions = read_json(wd.suggestions_json).get("suggestions", [])
        order = [s["id"] for s in suggestions]
        decisions_by_id = _load_decisions_by_id(wd)
        decisions_by_id.pop(sid, None)
        _save_decisions(wd, decisions_by_id, order)
        return jsonify({"ok": True, "summary": _decision_summary(decisions_by_id)})

    @app.route("/runs/<name>/markdown", methods=["GET", "POST"])
    def markdown_view(name):
        wd = _wd_or_404(name)

        if request.method == "POST":
            wd = _demo_fork_if_baseline(wd, name)
            text = request.form.get("content") or ""
            write_text_atomic(wd.final_md, text)

            # Optional filename update piggybacked on the same POST.
            raw_fn = (request.form.get("pdf_filename") or "").strip()
            if raw_fn:
                meta_now = _load_meta(wd)
                default = f"{wd.root.name}-resume.pdf"
                meta_now["pdf_filename"] = _safe_pdf_filename(raw_fn, default)
                write_json_atomic(wd.meta_json, meta_now)

            if request.form.get("rebuild") == "1":
                try:
                    run_build(wd)
                except Exception as e:
                    flash(f"Saved markdown but build failed: {e}", "error")
                    return redirect(url_for("markdown_view", name=name))
                # Send the (typically _blank-targeted) tab straight to the PDF.
                return redirect(url_for("pdf_view", name=name))
            flash("Saved.", "success")
            return redirect(url_for("markdown_view", name=name))

        if wd.final_md.exists():
            content = read_text(wd.final_md)
            source = "03-final.md"
        elif wd.resume_md.exists():
            content = read_text(wd.resume_md)
            source = "input/resume.md (final not generated yet — saving will create 03-final.md)"
        else:
            content = ""
            source = "(empty)"
        info = _run_info(wd.root)
        meta = _load_meta(wd)
        return render_template(
            "markdown.html",
            name=name,
            content=content,
            source=source,
            info=info,
            meta=meta,
        )

    @app.route("/runs/<name>/style", methods=["GET", "POST"])
    def style_view(name):
        wd = _wd_or_404(name)

        if request.method == "POST":
            wd = _demo_fork_if_baseline(wd, name)
            new_style: dict = {}
            errors: list[str] = []
            for k in DEFAULT_STYLE:
                raw = (request.form.get(k) or "").strip()
                if not raw:
                    continue
                try:
                    new_style[k] = float(raw)
                except ValueError:
                    errors.append(f"{k!r} must be a number (got {raw!r})")

            if errors:
                for e in errors:
                    flash(e, "error")
                return redirect(url_for("style_view", name=name))

            write_json_atomic(wd.style_json, new_style)

            if request.form.get("rebuild") == "1":
                try:
                    run_build(wd)
                    flash("Saved style and rebuilt PDF.", "success")
                except Exception as e:
                    flash(f"Saved style but build failed: {e}", "error")
            else:
                flash("Saved style.", "success")
            return redirect(url_for("style_view", name=name))

        style = load_style(wd)
        info = _run_info(wd.root)
        return render_template(
            "style.html",
            name=name,
            style=style,
            defaults=DEFAULT_STYLE,
            presets=PRESETS,
            has_overrides=wd.style_json.exists(),
            info=info,
        )

    @app.route("/runs/<name>/pdf")
    def pdf_view(name):
        wd = _wd_or_404(name)
        download = request.args.get("download") == "1"
        meta = _load_meta(wd)
        filename = meta["pdf_filename"]

        if wd.final_pdf.exists():
            return send_file(
                wd.final_pdf,
                mimetype="application/pdf",
                as_attachment=download,
                download_name=filename if download else None,
            )
        if wd.final_html.exists():
            # Match the chosen filename's stem for HTML downloads.
            html_name = filename.rsplit(".", 1)[0] + ".html"
            return send_file(
                wd.final_html,
                mimetype="text/html",
                as_attachment=download,
                download_name=html_name if download else None,
            )
        abort(404)

    @app.route("/runs/<name>/meta", methods=["POST"])
    def update_meta(name):
        wd = _wd_or_404(name)
        wd = _demo_fork_if_baseline(wd, name)
        meta = _load_meta(wd)
        raw = request.form.get("pdf_filename", "")
        default = f"{wd.root.name}-resume.pdf"
        meta["pdf_filename"] = _safe_pdf_filename(raw, default)
        write_json_atomic(wd.meta_json, meta)
        flash(f"Filename set to '{meta['pdf_filename']}'.", "success")
        return redirect(request.referrer or url_for("run_view", name=name))

    @app.route("/runs/<name>/rebuild", methods=["POST"])
    def rebuild_and_open(name):
        """Optionally update the PDF filename, run the build, then redirect to
        the PDF view. Designed to be POSTed with formtarget="_blank" so the
        PDF opens in a new tab while the caller stays where it was."""
        wd = _wd_or_404(name)
        wd = _demo_fork_if_baseline(wd, name)

        raw = (request.form.get("pdf_filename") or "").strip()
        if raw:
            meta = _load_meta(wd)
            default = f"{wd.root.name}-resume.pdf"
            meta["pdf_filename"] = _safe_pdf_filename(raw, default)
            write_json_atomic(wd.meta_json, meta)

        try:
            run_build(wd)
        except Exception as e:
            flash(f"Build failed: {e}", "error")
            return redirect(url_for("run_view", name=name))
        return redirect(url_for("pdf_view", name=name))

    @app.route("/runs/<name>/input/resume.md")
    def view_input_resume(name):
        wd = _wd_or_404(name)
        if not wd.resume_md.exists():
            abort(404)
        return send_file(wd.resume_md, mimetype="text/markdown; charset=utf-8")

    @app.route("/runs/<name>/input/job.txt")
    def view_input_jd(name):
        wd = _wd_or_404(name)
        if not wd.job_txt.exists():
            abort(404)
        return send_file(wd.job_txt, mimetype="text/plain; charset=utf-8")

    return app


def main() -> None:
    app = create_app()
    host = os.environ.get("RESUME_TAILOR_HOST", "127.0.0.1")
    port = int(os.environ.get("RESUME_TAILOR_PORT", "8000"))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    print(f"\n  resume-tailor → http://{host}:{port}\n", flush=True)
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    main()
