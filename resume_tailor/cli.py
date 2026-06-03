"""Command-line entry point.

Usage::

    python -m resume_tailor review  --resume R.md --jd J.txt --workdir runs/foo
    python -m resume_tailor approve --workdir runs/foo
    python -m resume_tailor apply   --workdir runs/foo
    python -m resume_tailor build   --workdir runs/foo
    python -m resume_tailor run     --resume R.md --jd J.txt --workdir runs/foo
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from resume_tailor import __version__
from resume_tailor.state import open_workdir


def _maybe_load_dotenv() -> None:
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv()
    except ImportError:
        pass  # optional


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="resume-tailor",
        description="Tailor a resume to a job description with Claude. Four-stage pipeline; each stage is independently re-runnable.",
    )
    p.add_argument("--version", action="version", version=f"resume-tailor {__version__}")

    sub = p.add_subparsers(dest="command", required=True)

    # review
    pr = sub.add_parser("review", help="Stage 1: ask Claude for suggested edits.")
    pr.add_argument("--resume", required=True, help="Path to your resume in Markdown.")
    pr.add_argument("--jd", required=True, help="Path to the job description (plain text).")
    pr.add_argument("--workdir", required=True, help="Per-application working directory.")
    pr.add_argument("--force", action="store_true", help="Regenerate suggestions even if 01-suggestions.json exists.")
    pr.add_argument("--model", help="Override the Claude model.")

    # approve
    pa = sub.add_parser("approve", help="Stage 2: walk through suggestions and approve/deny/edit.")
    pa.add_argument("--workdir", required=True)
    pa.add_argument("--restart", action="store_true", help="Discard existing decisions and start over.")

    # apply
    pap = sub.add_parser("apply", help="Stage 3: apply approved decisions to produce 03-final.md.")
    pap.add_argument("--workdir", required=True)

    # build
    pb = sub.add_parser("build", help="Stage 4: render 03-final.md to PDF.")
    pb.add_argument("--workdir", required=True)
    pb.add_argument("--html-only", action="store_true", help="Skip PDF rendering, just write 04-resume.html.")

    # run-all
    pall = sub.add_parser("run", help="Run all four stages in sequence.")
    pall.add_argument("--resume", required=True)
    pall.add_argument("--jd", required=True)
    pall.add_argument("--workdir", required=True)
    pall.add_argument("--force", action="store_true")
    pall.add_argument("--model")
    pall.add_argument("--html-only", action="store_true")

    # render (one-shot Markdown -> PDF, no LLM, no workdir)
    prend = sub.add_parser(
        "render",
        help="Render a Markdown file directly to PDF — no Claude, no workdir.",
    )
    prend.add_argument("--input", required=True, help="Path to your Markdown resume.")
    prend.add_argument("--output", required=True, help="Path to write the PDF (or HTML if --html-only).")
    prend.add_argument("--preset", help="Use a named style preset (default/compact/ultra-compact).")
    prend.add_argument("--style", help="Path to a style.json file with layout overrides.")
    prend.add_argument("--html-only", action="store_true", help="Write HTML instead of PDF.")

    # serve (web UI)
    psv = sub.add_parser("serve", help="Launch the local web UI on http://127.0.0.1:8000.")
    psv.add_argument("--host", default="127.0.0.1", help="Bind address (default 127.0.0.1).")
    psv.add_argument("--port", type=int, default=8000, help="Port (default 8000).")
    psv.add_argument("--debug", action="store_true", help="Enable Flask debug mode.")

    return p


def main(argv: list[str] | None = None) -> int:
    _maybe_load_dotenv()
    args = build_parser().parse_args(argv)

    # `render` is a one-shot — no workdir.
    if args.command == "render":
        import json as _json
        from resume_tailor.build import PRESETS, render_markdown

        input_path = Path(args.input)
        if not input_path.exists():
            print(f"Input not found: {input_path}", file=sys.stderr)
            return 2

        style: dict = {}
        if args.preset:
            if args.preset not in PRESETS:
                print(
                    f"Unknown preset {args.preset!r}. Choose one of: "
                    f"{', '.join(PRESETS)}",
                    file=sys.stderr,
                )
                return 2
            style.update(PRESETS[args.preset])
        if args.style:
            style_path = Path(args.style)
            if not style_path.exists():
                print(f"Style file not found: {style_path}", file=sys.stderr)
                return 2
            with style_path.open() as f:
                style.update(_json.load(f))

        md_text = input_path.read_text(encoding="utf-8")
        render_markdown(
            md_text,
            Path(args.output),
            style=style or None,
            html_only=args.html_only,
        )
        return 0

    # `serve` is the only other command without a workdir.
    if args.command == "serve":
        from resume_tailor.web.app import create_app
        app = create_app()
        print(f"\n  resume-tailor → http://{args.host}:{args.port}\n", flush=True)
        app.run(host=args.host, port=args.port, debug=args.debug)
        return 0

    wd = open_workdir(args.workdir)

    if args.command == "review":
        from resume_tailor.review import run_review
        run_review(
            wd,
            Path(args.resume),
            Path(args.jd),
            force=args.force,
            model=args.model,
        )
        return 0

    if args.command == "approve":
        from resume_tailor.approve import run_approve
        run_approve(wd, restart=args.restart)
        return 0

    if args.command == "apply":
        from resume_tailor.apply import run_apply
        run_apply(wd)
        return 0

    if args.command == "build":
        from resume_tailor.build import run_build
        run_build(wd, html_only=args.html_only)
        return 0

    if args.command == "run":
        from resume_tailor.review import run_review
        from resume_tailor.approve import run_approve
        from resume_tailor.apply import run_apply
        from resume_tailor.build import run_build

        run_review(
            wd,
            Path(args.resume),
            Path(args.jd),
            force=args.force,
            model=args.model,
        )
        run_approve(wd)
        run_apply(wd)
        run_build(wd, html_only=args.html_only)
        return 0

    print(f"Unknown command: {args.command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
