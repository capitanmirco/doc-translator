# doc-translator

A [GitHub Copilot CLI](https://githubnext.com/projects/copilot-cli) skill that translates large documents (PDF, DOCX, PPTX, HTML, TXT, MD…) into any target language, with automatic Markdown cleanup and intelligent chunking to bypass model context limits.

## Features

- 🌍 **Auto-detect source language** — no need to specify it
- 📄 **Any format → Markdown** — uses docling → markitdown as conversion chain
- ✂️ **Smart chunking** — splits at heading/paragraph boundaries, never mid-sentence
- 📝 **Living glossary** — builds a term glossary as it translates, keeps it consistent across chunks
- ♻️ **Resumable** — skips already-translated chunks if interrupted
- 🧹 **Artifact cleanup** — removes PDF-conversion noise (broken hyphenation, repeated headers/footers, stray page numbers)
- 🔍 **Environment check** — verifies Python, pip, and conversion tools before starting; installs what's missing

## Install

```bash
npx skills add capitanmirco/doc-translator
```

## Usage

Just describe what you want in Copilot Chat:

```
Translate report.pdf to Italian
Translate my-notes.docx to Spanish, aggressive cleanup
Translate README.md to French
```

## Requirements

- Python 3.8+
- `docling` or `markitdown` (installed automatically if missing)
- GitHub Copilot CLI , Claude Code or any coding agents CLI

## Helper script

`chunk_doc.py` is a standalone Python 3.8+ script used by the skill to split, clean, merge and track translation progress. It is invoked by the agent — no manual usage needed.

## License

MIT
