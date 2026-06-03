"""Stage 4: render the final markdown to an ATS-friendly PDF.

Pipeline: markdown → HTML → PDF (WeasyPrint).

If WeasyPrint can't load (missing system libs), fall back to writing the styled
HTML and tell the user to print-to-PDF from a browser.

Layout tuning
-------------

The stylesheet uses CSS variables for all the things you typically want to
tweak when a resume runs long: body font size, line height, page margins,
heading sizes, and bullet spacing. Defaults live in :data:`DEFAULT_STYLE`.

Each workdir may contain an optional ``style.json`` with overrides; any keys
that match :data:`DEFAULT_STYLE` are turned into a ``:root`` block appended
to the stylesheet at build time. Missing keys fall back to defaults. The
:data:`PRESETS` dict provides a few named starting points (``default``,
``compact``, ``ultra-compact``) — the web UI exposes one-click buttons for
these, but you can also just write them to ``style.json`` by hand.
"""

from __future__ import annotations

import sys
from pathlib import Path

import markdown

from resume_tailor.state import Workdir, read_json, read_text, write_text_atomic


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


# ---------------------------------------------------------------------------
# Style tunables
# ---------------------------------------------------------------------------

DEFAULT_STYLE: dict[str, float] = {
    "font_size_pt":          10.5,
    "line_height":           1.35,
    "page_margin_top_in":    0.6,
    "page_margin_side_in":   0.7,
    "name_size_pt":          20.0,
    "section_size_pt":       11.0,
    "subsection_size_pt":    10.5,
    "bullet_spacing_in":     0.025,
    "section_spacing_in":    0.18,
    "subsection_spacing_in": 0.10,
}


PRESETS: dict[str, dict[str, float]] = {
    "default": DEFAULT_STYLE,
    "compact": {
        "font_size_pt":          10.0,
        "line_height":           1.3,
        "page_margin_top_in":    0.5,
        "page_margin_side_in":   0.6,
        "name_size_pt":          18.0,
        "section_size_pt":       10.5,
        "subsection_size_pt":    10.0,
        "bullet_spacing_in":     0.02,
        "section_spacing_in":    0.14,
        "subsection_spacing_in": 0.08,
    },
    "ultra-compact": {
        "font_size_pt":          9.5,
        "line_height":           1.25,
        "page_margin_top_in":    0.4,
        "page_margin_side_in":   0.5,
        "name_size_pt":          16.0,
        "section_size_pt":       10.0,
        "subsection_size_pt":    9.5,
        "bullet_spacing_in":     0.015,
        "section_spacing_in":    0.12,
        "subsection_spacing_in": 0.06,
    },
}


# Map JSON keys to (CSS variable name, unit suffix). Empty unit means unitless
# (i.e. line_height).
_KEY_TO_CSS_VAR: dict[str, tuple[str, str]] = {
    "font_size_pt":          ("--font-size", "pt"),
    "line_height":           ("--line-height", ""),
    "page_margin_top_in":    ("--margin-top", "in"),
    "page_margin_side_in":   ("--margin-side", "in"),
    "name_size_pt":          ("--name-size", "pt"),
    "section_size_pt":       ("--section-size", "pt"),
    "subsection_size_pt":    ("--subsection-size", "pt"),
    "bullet_spacing_in":     ("--bullet-spacing", "in"),
    "section_spacing_in":    ("--section-spacing", "in"),
    "subsection_spacing_in": ("--subsection-spacing", "in"),
}


def load_style(wd: Workdir) -> dict[str, float]:
    """Return the effective style for *wd*: defaults overlaid with any
    matching keys from ``style.json``."""
    style = dict(DEFAULT_STYLE)
    if wd.style_json.exists():
        overrides = read_json(wd.style_json) or {}
        for k, v in overrides.items():
            if k in DEFAULT_STYLE:
                try:
                    style[k] = float(v)
                except (TypeError, ValueError):
                    pass  # silently skip bad values; defaults win
    return style


def _style_to_css(style: dict[str, float]) -> str:
    """Build a ``:root { ... }`` block from a style dict.

    Appended after the base stylesheet so it wins via specificity-equal,
    later-wins ordering.
    """
    lines = ["/* style overrides */", ":root {"]
    for key, (var, unit) in _KEY_TO_CSS_VAR.items():
        if key in style:
            lines.append(f"  {var}: {style[key]}{unit};")
    lines.append("}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _styles_path() -> Path:
    return Path(__file__).parent / "styles" / "resume.css"


def _markdown_to_html(md_text: str) -> str:
    return markdown.markdown(
        md_text,
        extensions=["extra", "sane_lists", "smarty"],
        output_format="html5",
    )


def _build_html(md_text: str, css_text: str, overrides_css: str) -> str:
    full_css = css_text + "\n" + overrides_css
    return HTML_DOC.format(css=full_css, body=_markdown_to_html(md_text))


def render_markdown(
    md_text: str,
    output_path: Path,
    *,
    style: dict | None = None,
    html_only: bool = False,
) -> Path:
    """Render Markdown to PDF (or HTML). No workdir required.

    This is the building block used by both :func:`run_build` (workdir mode)
    and the ``render`` CLI subcommand (one-shot mode).

    Args:
        md_text: The Markdown content to render.
        output_path: Where to write the result. The file is replaced atomically.
        style: Optional dict of layout overrides; keys must match
            :data:`DEFAULT_STYLE`. Unknown keys are ignored; missing keys fall
            back to defaults.
        html_only: If True, write the styled HTML instead of a PDF. Useful when
            WeasyPrint's system libraries aren't available, or for previewing
            in a browser.

    Returns:
        The path that was actually written.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    effective_style = dict(DEFAULT_STYLE)
    if style:
        for k, v in style.items():
            if k in DEFAULT_STYLE:
                try:
                    effective_style[k] = float(v)
                except (TypeError, ValueError):
                    pass

    css_text = _styles_path().read_text(encoding="utf-8")
    overrides_css = _style_to_css(effective_style)
    html = _build_html(md_text, css_text, overrides_css)

    if html_only:
        write_text_atomic(output_path, html)
        print(f"[render] Wrote {output_path}", file=sys.stderr)
        return output_path

    try:
        from weasyprint import HTML  # type: ignore
    except (ImportError, OSError) as exc:
        # Same fallback as run_build: write the HTML next to the requested PDF
        # so the user can print-to-PDF from a browser.
        fallback = output_path.with_suffix(".html")
        write_text_atomic(fallback, html)
        print(
            f"[render] WeasyPrint unavailable ({exc}). Wrote {fallback} as a "
            f"fallback — open it in your browser and print to PDF.",
            file=sys.stderr,
        )
        return fallback

    HTML(string=html, base_url=str(output_path.parent)).write_pdf(str(output_path))
    print(f"[render] Wrote {output_path}", file=sys.stderr)
    return output_path


def run_build(wd: Workdir, *, html_only: bool = False) -> Path:
    if not wd.final_md.exists():
        raise FileNotFoundError(
            f"{wd.final_md} not found. Run `apply` first, or hand-write a "
            f"final markdown file at that path."
        )

    md_text = read_text(wd.final_md)
    css_text = _styles_path().read_text(encoding="utf-8")
    overrides_css = _style_to_css(load_style(wd))
    html = _build_html(md_text, css_text, overrides_css)

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
