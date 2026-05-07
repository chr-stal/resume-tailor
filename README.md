# resume-tailor

A small CLI that tailors your resume to a specific job description, using Claude
for the suggestions and a clean Markdown → HTML → PDF pipeline for the final
ATS-friendly output.

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

## Usage

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

## Fallback PDF rendering

If WeasyPrint won't install, run `build --html-only` to get a printable HTML
file at `04-resume.html`. Open it in your browser and use *File → Print →
Save as PDF*. The CSS is the same, so it still comes out ATS-friendly.

## Project layout

```
resume_tailor/
  __main__.py     # `python -m resume_tailor`
  cli.py          # argparse + subcommands
  state.py        # workdir paths, atomic JSON read/write
  review.py       # calls Claude API, writes 01-suggestions.json
  approve.py      # interactive prompt, writes 02-decisions.json
  apply.py        # deterministic patcher: resume.md + decisions → 03-final.md
  build.py        # markdown → HTML → PDF
  styles/
    resume.css    # ATS-friendly stylesheet
examples/
  resume.md       # sample resume
  job.txt         # sample job description
```
