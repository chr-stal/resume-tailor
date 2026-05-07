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
from pathlib import Path

from flask import (
    Flask,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from resume_tailor.apply import run_apply
from resume_tailor.build import run_build
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

# Slugs for run names. Lowercase, no spaces, no path traversal.
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = os.environ.get("RESUME_TAILOR_SECRET", "dev-resume-tailor")

    # Pull ANTHROPIC_API_KEY from .env if present.
    try:
        from dotenv import load_dotenv
        load_dotenv(PROJECT_ROOT / ".env")
    except ImportError:
        pass

    RUNS_ROOT.mkdir(parents=True, exist_ok=True)

    # ----- helpers -----
    def _wd_or_404(name: str) -> Workdir:
        if not SLUG_RE.match(name):
            abort(404)
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

    def _save_decisions(wd: Workdir, decisions_by_id: dict, order: list[str]) -> None:
        payload = {"decisions": [decisions_by_id[i] for i in order if i in decisions_by_id]}
        write_json_atomic(wd.decisions_json, payload)

    # ----- routes -----
    @app.route("/")
    def index():
        runs = []
        for p in sorted(RUNS_ROOT.iterdir()) if RUNS_ROOT.exists() else []:
            if p.is_dir() and not p.name.startswith("."):
                runs.append(_run_info(p))
        return render_template("index.html", runs=runs)

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
        run_dir = RUNS_ROOT / name
        if run_dir.exists():
            flash(f"Run '{name}' already exists.", "error")
            return redirect(url_for("index"))
        wd = open_workdir(run_dir)
        write_text_atomic(wd.resume_md, resume.rstrip() + "\n")
        write_text_atomic(wd.job_txt, jd.rstrip() + "\n")
        flash(f"Created run '{name}'.", "success")
        return redirect(url_for("run_view", name=name))

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
        return render_template(
            "run.html",
            name=name,
            info=info,
            suggestion_count=suggestion_count,
            decision_summary=decision_summary,
        )

    @app.route("/runs/<name>/review", methods=["POST"])
    def run_review_route(name):
        wd = _wd_or_404(name)
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
        try:
            run_apply(wd)
            flash("Applied decisions.", "success")
        except Exception as e:
            flash(f"Apply failed: {e}", "error")
        return redirect(url_for("run_view", name=name))

    @app.route("/runs/<name>/build", methods=["POST"])
    def run_build_route(name):
        wd = _wd_or_404(name)
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
            text = request.form.get("content") or ""
            write_text_atomic(wd.final_md, text)
            if request.form.get("rebuild") == "1":
                try:
                    run_build(wd)
                    flash("Saved and rebuilt PDF.", "success")
                except Exception as e:
                    flash(f"Saved markdown but build failed: {e}", "error")
            else:
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
        return render_template(
            "markdown.html",
            name=name,
            content=content,
            source=source,
            info=info,
        )

    @app.route("/runs/<name>/pdf")
    def pdf_view(name):
        wd = _wd_or_404(name)
        if wd.final_pdf.exists():
            return send_file(wd.final_pdf, mimetype="application/pdf")
        if wd.final_html.exists():
            return send_file(wd.final_html, mimetype="text/html")
        abort(404)

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
