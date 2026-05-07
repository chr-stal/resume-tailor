"""Stage 4: render the final markdown to an ATS-friendly PDF.

Pipeline: markdown → HTML → PDF (WeasyPrint).

If WeasyPrint can't load (missing system libs), fall back to writing the styled
HTML and tell the user to print-to-PDF from a browser.
"""

from __future__ import annotations

import sys
from pathlib import Path

import markdown

from resume_tailor.state import Workdir, read_text, write_text_atomic


HTML_DOC = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Resume</title>
<style>
{css}
</style>
</head>
<body>
{body}
</body>
</html>
"""


def _styles_path() -> Path:
    return Path(__file__).parent / "styles" / "resume.css"


def _markdown_to_html(md_text: str) -> str:
    return markdown.markdown(
        md_text,
        extensions=["extra", "sane_lists", "smarty"],
        output_format="html5",
    )


def _build_html(md_text: str, css_text: str) -> str:
    return HTML_DOC.format(css=css_text, body=_markdown_to_html(md_text))


def run_build(wd: Workdir, *, html_only: bool = False) -> Path:
    if not wd.final_md.exists():
        raise FileNotFoundError(
            f"{wd.final_md} not found. Run `apply` first, or hand-write a "
            f"final markdown file at that path."
        )

    md_text = read_text(wd.final_md)
    css_text = _styles_path().read_text(encoding="utf-8")
    html = _build_html(md_text, css_text)

    if html_only:
        write_text_atomic(wd.final_html, html)
        print(f"[build] Wrote {wd.final_html} (open in browser, then File → Print → Save as PDF)", file=sys.stderr)
        return wd.final_html

    try:
        from weasyprint import HTML  # type: ignore
    except ImportError as exc:
        write_text_atomic(wd.final_html, html)
        print(
            f"[build] WeasyPrint not installed ({exc}). Wrote {wd.final_html} "
            f"as a fallback — open it in your browser and print to PDF.",
            file=sys.stderr,
        )
        return wd.final_html
    except OSError as exc:
        # WeasyPrint imports can fail at runtime if Pango/Cairo are missing.
        write_text_atomic(wd.final_html, html)
        print(
            f"[build] WeasyPrint system libraries missing ({exc}). "
            f"Wrote {wd.final_html} as a fallback. On macOS run `brew install pango`.",
            file=sys.stderr,
        )
        return wd.final_html

    HTML(string=html, base_url=str(wd.root)).write_pdf(str(wd.final_pdf))
    print(f"[build] Wrote {wd.final_pdf}", file=sys.stderr)
    return wd.final_pdf
