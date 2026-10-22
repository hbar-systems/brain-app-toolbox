---
created: 2026-10-22
---

# Toolbox

Drop a file and pick an action.
The result lands in Files.

## What it does

Chips appear for the actions that fit the file you dropped.

- PDF: To text (.txt), To Word (.docx, paragraphs kept, a page break between pages), Split (one PDF per page in a zip, or a range like `1-3, 5` as one PDF), Merge (drop several PDFs, order them with Up and Down).
- Image (JPEG, PNG, WebP): Compress with Small, Balanced or High, an optional largest width (1600, 2400, original), keep the format or change to JPEG or WebP. Shows the size before and after.
- Audio: Transcribe through the brain's speech setup. The text comes back as a .txt file.
- PDF, Word, text or a transcript just made: Summarize and Translate (pick a language, English by default). Long text is read in parts of about 12,000 characters and the part summaries are summarized together. The result is a .md file.

Each result is saved to Files (`out/<date>/`), shown in a card with Open in Files and Download, and listed under Recent (the last ten).
On a brain with no Files desk, the result is downloaded instead and the card says so.
"Use this text" on a text result puts it back on the table so it can be summarized or translated.

## Permissions and why

- `files.write`: to put results in Files.
- `llm.invoke`: to summarize and translate with the brain's model.
- `audio.transcribe`: to transcribe audio through the brain's speech door.
- `requires_layers: semantic (read)`: the bridge requires one read layer with `llm.invoke`. Toolbox asks the model to use only the text it is given, and keeps the scope to this one layer.

Text from files goes into prompts inside `BF.untrusted(...)`, so the model reads it as data, never as instructions.
Model output is shown with `BF.md`, never as HTML.

## The helper (api.py)

PDF, Word and image work runs in `api.py`, mounted by the brain at `/apps/toolbox/api/`.
App backends are reached without the owner key, so it is pure computation: stateless, no memory, settings or keys, no network, nothing written to disk.
Input is capped at 25 MB decoded and 200 pages, and errors come back as `{"detail": "..."}` with 400 or 413.
It uses only `pypdf`, `pymupdf`, `python-docx` and `pillow`.

Scanned PDFs have no text layer, so To text and To Word say so instead of returning an empty file.

## How to try it

1. Push this repo to `github.com/hbar-systems/brain-app-toolbox`.
2. On your brain open Apps, choose to install an app, and give it that repo.
3. Open Toolbox from the tab bar and drop a PDF.
4. Pick To text, press Run, then Open in Files.

Without GitHub: clone the repo into `brain-apps/toolbox/` on the brain and `POST /apps/install {"path": "brain-apps/toolbox"}`.

## Checks

```
python3 ../brain-app-kit/kit/check.py .
node --check app.js
```

AGPL-3.0, see `LICENSE`.
