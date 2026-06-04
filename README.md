# resume-tailor

A small CLI **and local web app** that tailors your resume to a specific job
description, using Claude for the suggestions and a clean Markdown → HTML → PDF
pipeline for the final ATS-friendly output.

The pipeline is intentionally split into four independent stages, each writing
its output to a JSON or text file in a per-job *workdir*. Any stage can be
re-run on its own, so a crash in one stage never costs you the work from the
previous one.

```
review  → 01-suggestions.json   (Claude reads resume + JD, proposes edits)
approve → 02-decisions.json     (you approve / deny / edit each one)
apply   → 03-final.md           (deterministic: resume.md + decisions → final markdown)
build   → 04-resume.pdf         (markdown → HTML → ATS-friendly PDF)
```

## Why split it this way

Two failure modes worth handling:

1. **Something breaks after edits but before the PDF is built.** Re-run `build`.
   Or `apply` then `build`. Your approved edits in `02-decisions.json` are safe.
2. **You want to skip Claude entirely.** Hand-write a `03-final.md` and just run
   `build`. Or hand-edit `02-decisions.json` and run `apply` then `build`.

The `approve` stage also saves after every single decision, so if the terminal
dies halfway through approving 30 suggestions, the next run picks up from where
you left off.

## Install

```bash
git clone <this repo>
cd resume-tailor
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

WeasyPrint (the PDF renderer) needs a couple of system libraries. On macOS:

```bash
brew install pango
```

On Debian/Ubuntu:

```bash
sudo apt install libpango-1.0-0 libpangoft2-1.0-0
```

If you don't want to install WeasyPrint deps, see *Fallback* below.

## Setup

```bash
cp .env.example .env
# put your Anthropic API key in .env
```

## Usage — web UI (recommended)

```bash
python -m resume_tailor serve
# open http://127.0.0.1:8000
```

From the browser you can:

- create a new run by pasting your resume Markdown and the job description,
- run review with one click and watch suggestions populate,
- approve / deny / edit each suggestion as a card — every click autosaves
  to `02-decisions.json`, so you can close the tab any time,
- apply decisions, hand-edit the final Markdown in the in-browser editor,
- rebuild and preview the ATS-friendly PDF inline.

Each run lives in `runs/<your-run-name>/` on disk — same files, same
fault-tolerance story as the CLI (see *Recovery scenarios* below).

Custom host or port:

```bash
python -m resume_tailor serve --host 0.0.0.0 --port 9000
```

## Usage — CLI

```bash
# 1) Convert your resume to Markdown (or write it from scratch).
#    See examples/resume.md for a template.

# 2) Save the job description as plain text (paste from the posting).

# 3) Run the pipeline. The workdir is per-application — make a new one each time.
python -m resume_tailor review  --resume my_resume.md --jd job.txt --workdir runs/acme-pm
python -m resume_tailor approve --workdir runs/acme-pm
python -m resume_tailor apply   --workdir runs/acme-pm
python -m resume_tailor build   --workdir runs/acme-pm

# Or run all four in sequence:
python -m resume_tailor run --resume my_resume.md --jd job.txt --workdir runs/acme-pm
```

The PDF lands at `runs/acme-pm/04-resume.pdf`.

The CLI and the web UI write the same files in the same workdir layout, so
you can mix and match — e.g. drive the early stages from the web UI, then
hand-edit `03-final.md` and re-run `build` from the CLI.

## Usage — one-shot Markdown → PDF (no Claude)

When you've already written the Markdown you want — say, you're iterating on
your base resume — and just want a PDF, skip the pipeline entirely:

```bash
python -m resume_tailor render --input my_resume.md --output my_resume.pdf

# With a layout preset:
python -m resume_tailor render --input my_resume.md --output my_resume.pdf --preset compact

# With a custom style file:
python -m resume_tailor render --input my_resume.md --output my_resume.pdf --style style.json

# Or skip PDF and write HTML for browser print-to-PDF:
python -m resume_tailor render --input my_resume.md --output my_resume.html --html-only
```

In the web UI, the index page has a *Render Markdown to PDF (no Claude)* form
right next to the LLM-driven creation form. That one lands you directly in
the Markdown editor — no review, approve, or apply stage in the way.

## The workdir

After a full run, `runs/acme-pm/` contains:

```
input/
  resume.md            # copy of your original
  job.txt              # copy of the JD
01-suggestions.json    # Claude's proposed edits
02-decisions.json      # your approve/deny/edit calls
03-final.md            # the tailored resume in markdown
04-resume.pdf          # the rendered PDF
```

Everything is plain text or JSON. Diff-friendly, version-control-friendly,
no hidden state.

## Recovery scenarios

| What happened | What to do |
|---|---|
| API timed out during `review` | Just re-run `review`. Add `--force` if a partial file got written. |
| Terminal closed mid-`approve` | Re-run `approve`. It picks up at the first undecided suggestion. |
| You want to tweak a few decisions | Edit `02-decisions.json` directly, then `apply` + `build`. |
| You decided Claude's suggestions weren't useful | Hand-write `03-final.md` (or copy `input/resume.md`) and run `build`. |
| PDF rendered ugly | Edit `03-final.md` or `resume_tailor/styles/resume.css`, run `build`. |

## Tuning the layout for a long resume

When your resume runs to two pages and you want to squeeze it to one, you can
tune body font size, line height, page margins, heading sizes, and bullet
spacing — without touching the stylesheet itself.

In the web UI, click **Tune layout** on the Build card. The form has one-click
presets (`default` / `compact` / `ultra-compact`) plus a number input for each
individual lever. Hit *Save & rebuild PDF* and reload the PDF preview.

From the CLI, drop a `style.json` into the run's workdir:

```json
{
  "font_size_pt": 10.0,
  "line_height": 1.3,
  "page_margin_top_in": 0.5,
  "page_margin_side_in": 0.6
}
```

Any keys you omit fall back to defaults. Then re-run `build`:

```bash
python -m resume_tailor build --workdir runs/acme-pm
```

Under the hood the stylesheet exposes everything as CSS variables
(`--font-size`, `--margin-top`, `--margin-side`, `--name-size`,
`--section-size`, etc.), and the build step prepends a `:root { ... }` block
built from `style.json` so it wins over the defaults.

## Deploying a read-only demo

The repo includes a `demo-runs/` directory with three pre-baked workdirs
showing the tool at different points in the pipeline:

- `acme-staff-eng-completed` — full state, all stages done.
- `stripe-payments-in-progress` — suggestions exist, ~4/9 decisions made.
- `google-cloud-clickrun` — resume + JD only. Clicking *Run review* in demo
  mode copies a pre-staged suggestions file into place (no API call).

In **demo mode**, all writes are no-ops except for that one "click-to-run"
trick: creating new runs, saving decisions, editing markdown, rebuilding,
and changing layout all show a friendly *"Demo mode — not persisted"* flash.
The Anthropic API key isn't read, so the deploy is safe to put on a public URL.

Enable demo mode via env var:

```bash
RESUME_TAILOR_DEMO=1 python -m resume_tailor serve
```

On first boot, the app seeds `runs/` from `demo-runs/` for any workdirs that
aren't already there.

### Deploying to Fly.io (recommended for free-tier demo)

```bash
brew install flyctl       # one-time
flyctl auth login         # one-time
flyctl launch --no-deploy # accept the bundled fly.toml when prompted
flyctl deploy
```

The bundled `fly.toml` sets `auto_stop_machines = "stop"` and
`auto_start_machines = true`, so the app sleeps when idle and wakes on
traffic. Expected cost: ~$0 with light interview-grade traffic.

### Deploying anywhere with Docker

```bash
docker build -t resume-tailor-demo .
docker run -p 8080:8080 resume-tailor-demo
# open http://localhost:8080
```

Render, Railway, and any other Docker-friendly host work the same — just
ensure `RESUME_TAILOR_DEMO=1` is set.

## Fallback PDF rendering

If WeasyPrint won't install, run `build --html-only` to get a printable HTML
file at `04-resume.html`. Open it in your browser and use *File → Print →
Save as PDF*. The CSS is the same, so it still comes out ATS-friendly.

## Project layout

```
resume_tailor/
  __main__.py     # `python -m resume_tailor`
  cli.py          # argparse + subcommands (review/approve/apply/build/run/serve)
  state.py        # workdir paths, atomic JSON read/write
  review.py       # calls Claude API, writes 01-suggestions.json
  approve.py      # interactive prompt, writes 02-decisions.json
  apply.py        # deterministic patcher: resume.md + decisions → 03-final.md
  build.py        # markdown → HTML → PDF
  styles/
    resume.css    # ATS-friendly stylesheet
  web/
    app.py        # Flask app: thin wrapper over the pipeline functions
    templates/    # base, index, run dashboard, approve cards, markdown editor
    static/       # style.css + autosave JS for the approval view
examples/
  resume.md       # sample resume
  job.txt         # sample job description
```
