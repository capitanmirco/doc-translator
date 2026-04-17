---
name: doc-translator
description: >
  Translate large documents (PDF, DOCX, MD, TXT, PPTX, XLSX) into a user-chosen language.
  Handles chunking to bypass Sonnet 4.6 context limits, artifact cleanup from doc/pdf conversion,
  terminology consistency via a glossary, and progressive chunk files merged into a single output.
  Use this skill when the user asks to translate a document, manual, article, or any long text
  into another language.
---

# Doc Translator Skill

Translates large documents with:
- Full environment check and automatic installation of missing dependencies
- Artifact cleanup from PDF/DOCX → Markdown conversion (3 cleanup levels)
- Intelligent chunking to respect Sonnet 4.6 output limits (~8K tokens/response)
- Chunk-by-chunk intermediate files merged into a single final output
- Terminology glossary maintained across all chunks for consistency

**Helper script**: `~/.copilot/skills/doc-translator/chunk_doc.py` (requires Python 3.8+)

---

## Progress notification rule

> **Always keep the user informed at every step.** Before starting each step, announce what you are
> about to do. After completing it, confirm success or describe the issue clearly.

Use this format:
```
📌 Step N/9 — <what you are about to do>
✅ <result or confirmation>
```

---

## Step 0 — Environment Check

> 📌 **Step 0/9 — Checking your environment. This is done once before anything else.**

Run all checks and report a summary to the user before proceeding.

### 0a. Detect operating system

```bash
uname -s
```
- `Linux` → use `apt` (Debian/Ubuntu) or `dnf`/`yum` (RHEL/Fedora)
- `Darwin` → use `brew`
- Other → use pip-only methods; warn user if OS-level installs are unavailable

### 0b. Check Python

```bash
python3 --version 2>/dev/null || python --version 2>/dev/null || echo "NOT_FOUND"
```

**Python found and ≥ 3.8** → report version ✅  
**Python found but < 3.8** → must upgrade (see install below)  
**Python not found** → install:

```bash
# Debian/Ubuntu
sudo apt-get update -y && sudo apt-get install -y python3 python3-pip python3-venv

# RHEL / Fedora / CentOS
sudo dnf install -y python3 python3-pip || sudo yum install -y python3 python3-pip

# macOS
brew install python3

# Windows (PowerShell — if applicable)
winget install Python.Python.3.12
```

After installing, re-run: `python3 --version`  
If Python still cannot be installed, **stop and tell the user** — this skill requires Python 3.8+.

### 0c. Check pip

```bash
pip3 --version 2>/dev/null || python3 -m pip --version 2>/dev/null || echo "NOT_FOUND"
```

If not found:
```bash
python3 -m ensurepip --upgrade 2>/dev/null || \
  curl -sS https://bootstrap.pypa.io/get-pip.py | python3
```

### 0d. Check document conversion tools

Check in priority order:

```bash
python3 -c "import docling; print('docling OK')" 2>/dev/null          || echo "docling: not found"
python3 -c "import markitdown; print('markitdown OK')" 2>/dev/null    || echo "markitdown: not found"
test -f ~/.local/share/bruce-doc-converter/convert.sh && echo "bruce-doc-converter: OK" \
                                                       || echo "bruce-doc-converter: not found"
```

**If no tool is available**, install `docling` automatically and inform the user:
```
⏳ No conversion tool found. Installing docling (this may take a minute)...
```
```bash
pip3 install docling --break-system-packages 2>/dev/null || pip3 install docling
```

### 0e. Report environment summary to the user

```
📋 Environment check complete:
   ✅ Python 3.11.2
   ✅ pip 23.3
   ✅ docling 2.x  ← will be used for conversion
   ⚠️  markitdown : not installed (not required)
   ⚠️  bruce-doc-converter : not found (not required)

   ✅ All required dependencies are ready. Starting translation workflow...
```

---

## Step 1 — Gather input

> 📌 **Step 1/9 — Gathering input parameters...**

Ask the user for the following (if not already provided):

1. **Source file path** — supported formats: `.md`, `.txt`, `.docx`, `.pdf`, `.pptx`, `.xlsx`
2. **Target language** — e.g.: "Italian", "French", "German", "Spanish", "Portuguese", "Dutch", "Japanese", etc.
3. **Artifact cleanup level** — always ask explicitly:
   - `light` — minimal cleanup: isolated page numbers, broken hyphenation (`word-\n`), excess blank lines
   - `medium` *(recommended for PDF/DOCX)* — light + malformed headings, misindented lists, repeated page headers/footers
   - `aggressive` — medium + soft-hyphen removal, OCR anomalies, broken short lines rejoined

> If the user does not specify a cleanup level, **suggest `medium`** and proceed with it unless they say otherwise.

Confirm the collected parameters:
```
✅ Source  : <file_path>
✅ Target  : <language>
✅ Cleanup : <level>
```

---

## Step 2 — Compute output path

> 📌 **Step 2/9 — Computing output file path...**

Derive `OUTPUT_PATH` from the source file. Save it in memory — it will be used in Step 7.

| Source file | `OUTPUT_PATH` |
|-------------|---------------|
| `/path/manual.pdf`   | `/path/manual.md` |
| `/path/report.docx`  | `/path/report.md` |
| `/path/notes.txt`    | `/path/notes.md`  |
| `/path/doc.md` *(already Markdown)* | `/path/doc_translated.md` |

**Rule**: same directory, same filename stem, `.md` extension.  
**Exception**: if source is already `.md`, append `_translated` to avoid overwriting the original.

Tell the user:
```
📄 Output will be saved to: <OUTPUT_PATH>
```

---

## Step 3 — Convert to Markdown (skip if source is already .md or .txt)

> 📌 **Step 3/9 — Converting `<filename>` to Markdown...**

Use the first available tool in this priority chain. Try the next option only if the current one fails or produces an empty file.

### Option A — docling *(preferred — best quality for complex PDFs, tables, multi-column layouts)*

Install if missing:
```bash
pip3 install docling --break-system-packages 2>/dev/null || pip3 install docling
```
> ⏳ Installing docling — please wait...

Convert:
```bash
docling /path/to/source_file --output-dir /same/directory/as/source/
```

`docling` creates `<stem>.md` in the output directory. Use that file for all subsequent steps.

If docling fails on a scanned or encrypted PDF, retry with explicit OCR pipeline:
```bash
docling /path/to/source_file --output-dir /same/directory/ --pipeline standard
```

If it still fails → try **Option B**.

### Option B — markitdown *(lightweight — great for DOCX, PPTX, HTML)*

Install if missing:
```bash
pip3 install markitdown --break-system-packages 2>/dev/null || pip3 install markitdown
```
> ⏳ Installing markitdown...

Convert:
```bash
python3 -m markitdown /path/to/source_file > /same/directory/<stem>.md
```

Verify the output is not empty:
```bash
wc -c < /same/directory/<stem>.md
```
If the result is `0` or less than 100 bytes → try **Option C**.

### Option C — bruce-doc-converter *(fallback)*

```bash
bash ~/.local/share/bruce-doc-converter/convert.sh /path/to/source_file
```

Read the JSON response:
- `success: true` → use `output_path` as input for next steps
- `success: false` → read `error` field; try to address it or fall through to the failure case

### If all tools fail

Tell the user:
```
❌ Could not convert the file automatically (tried: docling, markitdown, bruce-doc-converter).
   Possible reasons: DRM/password protection, scanned image PDF with no text layer, unsupported format.
   Please convert the file manually to .txt or .md and share it so we can continue.
```

### After successful conversion

Tell the user:
```
✅ Converted to Markdown: <converted_md_path>
   Proceeding to chunking...
```

> Note: the converted `.md` file is used only as **input for chunking** in Step 4.
> `OUTPUT_PATH` (set in Step 2) is not changed — the final translated file will be saved there.

---

## Step 4 — Chunk and cleanup

> 📌 **Step 4/9 — Cleaning artifacts and splitting document into chunks (persistent session)...**

First, check for an existing session that can be resumed:
```bash
python3 ~/.copilot/skills/doc-translator/chunk_doc.py list
```

- If a session exists for the same source file with chunks already done → ask the user:
  `"Found session <ID> with N/TOTAL chunks done. Resume it? (yes/no)"`
  - **yes** → set `SESSION_ID` to the existing session, skip to Step 5 (glossary already in state)
  - **no** → create a new session below

If splitting fresh:
```bash
python3 ~/.copilot/skills/doc-translator/chunk_doc.py split \
  --file <INTERMEDIATE_MD_PATH> \
  --level <cleanup_level> \
  --words 2800 \
  --target-lang "<TARGET_LANGUAGE>" \
  --batch-size 8
```

> Use `--words 2000` for highly expansive language pairs (e.g., English → German)
> or if a chunk translation gets truncated in Step 6.

Read the JSON output. Save `SESSION_ID`, `CHUNK_COUNT`, `TOTAL_WORDS`, `SOURCE_LANG`, `OUTPUT_PATH` in memory.

Tell the user:
```
✅ Document split into N chunks (total: X words).
🌍 Source language detected: <source_lang> → <target_lang>
🔑 Session ID: <session_id>
📁 Session saved permanently — safe to close and resume later.
▶️  This session will translate up to 8 chunks per run. Run the skill again to continue.
```

---

## Step 5 — Extract initial glossary

> 📌 **Step 5/9 — Extracting terminology glossary...**

Load the session state to check if a glossary already exists:
```bash
python3 ~/.copilot/skills/doc-translator/chunk_doc.py load-state --session <SESSION_ID>
```

If `glossary` is non-empty (resuming) → use the saved glossary and skip to Step 6.

If starting fresh, read the first chunk:
```bash
cat <SESSION_DIR>/src_chunk_001.txt
```

Build a **glossary** of terms to keep consistent across all chunks. Include:
- Proper names: people, places, organizations, products, brands
- Technical terms, acronyms, abbreviations
- Terms that must stay in the source language (URLs, shell commands, file names, standards)
- Terms with a domain-specific translation preference

Save the initial glossary into the session state:
```bash
python3 ~/.copilot/skills/doc-translator/chunk_doc.py update-glossary \
  --session <SESSION_ID> \
  --terms '{"term1": "translation1", "term2": "keep as-is"}'
```

Tell the user:
```
✅ Glossary initialized with N terms.
```

---

## Step 6 — Translate chunk by chunk (batch mode)

> 📌 **Step 6/9 — Translating (batch of up to 8 chunks per agent session)...**

### 6a. Get the next batch of untranslated chunks

```bash
python3 ~/.copilot/skills/doc-translator/chunk_doc.py next-batch \
  --session <SESSION_ID> \
  --batch-size 8
```

This returns a JSON object with:
- `batch` — array of chunks to translate (each has `chunk_number`, `src_path`, `trl_path`, `content`)
- `done` / `total` — progress counts
- `target_lang`, `source_lang`, `glossary` — loaded from persistent state
- `remaining_after_batch` — chunks still pending after this batch
- `is_complete` — true when no chunks remain

If `batch` is empty and `is_complete` is true → skip to Step 7 (all done).

Tell the user:
```
⏳ Translating chunks N to M of TOTAL (X chunks remaining after this batch)...
```

### 6b. For each chunk in the batch

For each item in `batch`:

**Announce:**
```
⏳ Chunk N/TOTAL (~X words)...
```

**Translate** using this internal prompt:

---
**TRANSLATION PROMPT** (substitute all placeholders before applying):

```
You are a professional translator specializing in [DOCUMENT DOMAIN, if detectable].
Translate the following text into [TARGET_LANGUAGE].

RULES:
1. Preserve ALL Markdown formatting: headings (#, ##, ###), lists (-, *), bold (**),
   italic (*), code (`inline` and ```blocks```), tables (|), links ([text](url)), blockquotes (>)
2. Do NOT translate: source code, shell commands, URLs, file paths, variables, file names
3. Do NOT translate terms marked "keep as-is" in the glossary below
4. Use natural, fluent [TARGET_LANGUAGE] — avoid literal word-for-word translation
5. Maintain paragraph structure and approximate length
6. If you spot remaining PDF/conversion artifacts (isolated numbers, broken words,
   anomalous characters), silently fix them during translation
7. After the translated text, if you encounter new technical terms or proper names not in the
   glossary, list them under "NEW_TERMS:" (one per line, format: "term" → "translation or keep-as-is")

CURRENT GLOSSARY:
[GLOSSARY as key: value pairs]

---START OF TEXT TO TRANSLATE---
[CHUNK_CONTENT]
---END OF TEXT---

Output format:
1. The complete translated text in valid Markdown
2. Optionally: a "NEW_TERMS:" section at the very end
Do NOT add any preamble, explanation, or comment outside these two sections.
```

---

**Save** the translated text (without the `NEW_TERMS:` section) using:
```bash
python3 ~/.copilot/skills/doc-translator/chunk_doc.py save-chunk \
  --session <SESSION_ID> \
  --chunk <N> \
  --file <path_to_tmp_file_with_translation>
```

Or write directly to `trl_path` from the batch JSON (e.g., `~/.copilot/doc-translator/sessions/<SESSION_ID>/trl_chunk_NNN.md`).

**Update glossary** if the response includes new terms:
```bash
python3 ~/.copilot/skills/doc-translator/chunk_doc.py update-glossary \
  --session <SESSION_ID> \
  --terms '{"new_term": "translation"}'
```

**Confirm:**
```
✅ Chunk N/TOTAL translated and saved.
```

### 6c. End of batch — report and pause if needed

After translating all chunks in the batch:

```bash
python3 ~/.copilot/skills/doc-translator/chunk_doc.py status --session <SESSION_ID>
```

**If `remaining > 0`** (more chunks to translate):
```
⏸️  Batch complete: N/TOTAL chunks done (REMAINING remaining).
   Context limit reached for this session.
   👉 Run the skill again with the same file to automatically resume from chunk M.
   Session ID: <SESSION_ID> (saved permanently — no data will be lost)
```
→ **STOP HERE.** Do not proceed to Step 7. The user must re-invoke the skill.

**If `remaining == 0`** (all chunks done):
```
✅ All TOTAL chunks translated! Proceeding to merge...
```
→ Continue to Step 7.

---

## Step 7 — Merge into final output

> 📌 **Step 7/9 — Merging all translated chunks into the final file...**

```bash
python3 ~/.copilot/skills/doc-translator/chunk_doc.py merge \
  --session <SESSION_ID>
```

The `--output` path is optional — if omitted, the path from `state.json` is used automatically.

If `success: false` → read `"error"` and verify all translated chunk files exist and are non-empty.

If `"warning"` is present (not all chunks done) → do NOT merge; tell the user to resume translation first.

Tell the user:
```
✅ Merged N chunks → <OUTPUT_PATH>
```

---

## Step 8 — Clean up temporary files

> 📌 **Step 8/9 — Cleaning up...**

The **session directory** (`~/.copilot/doc-translator/sessions/<SESSION_ID>/`) is preserved permanently
so the session can always be resumed or inspected. Only clean it if the user explicitly asks.

Remove only the intermediate conversion file (if the source was not .md):
```bash
rm -f <INTERMEDIATE_MD_PATH>   # only if it was a temp conversion, not the original
```

```
✅ Temporary conversion files removed. Session data preserved at:
   ~/.copilot/doc-translator/sessions/<SESSION_ID>/
```

---

## Step 9 — Final summary

> 📌 **Step 9/9 — Done!**

```
✅ Translation complete!

📄 Output file  : <OUTPUT_PATH>
📊 Stats        : N chunks | X words translated
🌍 Translation  : <lang_hint> → <target_language>
🔧 Cleanup level: <level>
📖 Glossary     : N terms managed
```

---

## Error handling reference

| Error | Cause | Solution |
|-------|-------|----------|
| Python not found | Python not installed | Install Python 3.8+ (Step 0b) |
| `pip3: command not found` | pip not installed | Run `python3 -m ensurepip --upgrade` |
| `chunk_doc.py: File not found` | Wrong path or file removed | Ask user for the absolute path |
| docling / markitdown fails | Scanned PDF, DRM, corrupted file | Try next tool in chain; ask for `.txt` as last resort |
| Conversion output is empty | Tool extracted no text layer | Try alternative conversion tool |
| Chunk translation truncated | Chunk too long for model output | Reduce `--words` to 2000 and re-run split with a new `--session` |
| `merge: No files found` | Session dir missing or wrong session ID | Run `chunk_doc.py list` to find the correct session ID |
| Output file already exists | Source was `.md` → `OUTPUT_PATH` = `doc_translated.md` | Normal behavior — warn user before overwriting |
| Glossary inconsistency | Same term translated differently across chunks | Do a search-and-replace pass on the final output to unify |
| Chunk already done (resume) | Session was interrupted and restarted | `status` shows completed chunks → auto-skip those with `"done": true` |

---

## Translation quality notes

### Always preserve
- **Code blocks** (` ``` `) → never translate content inside code fences
- **URLs and links** → keep intact; you may translate the display text if it is descriptive
- **Markdown tables** → keep `|col|col|` structure; translate only cell content
- **Numbers and dates** → respect the original document format
- **Titles of works / laws / standards** → keep in original language, use quotes or italics

### Terminology consistency
- The glossary is the primary tool for consistency
- For **technical documents**: prefer keeping English terms if they are commonly used in the target language
- For **literary / legal documents**: prefer more literal, formal translations

### Sonnet 4.6 output limit
- Practical output limit: ~8K tokens (~6000 words)
- Default 2800 words/chunk is calibrated for European languages with 10–20% expansion during translation
- For highly expansive languages (German, Finnish) or contractive ones (Chinese, Japanese): adjust `--words`

---

## Full session example

```
User: "Translate ~/Documents/technical_manual.pdf to Italian, medium cleanup"

Step 0: Environment check
   ✅ Python 3.11.2 | ✅ pip 23.3 | docling: not found → installing...
   ✅ docling 2.1.0 installed
   ✅ All dependencies ready.

Step 1: Input gathered
   Source: ~/Documents/technical_manual.pdf | Target: Italian | Cleanup: medium

Step 2: Output path computed
   📄 ~/Documents/technical_manual.md

Step 3: PDF → Markdown (via docling)
   ⏳ Converting...
   ✅ ~/Documents/technical_manual.md

Step 4: Chunk + cleanup
   ✅ 12 chunks | 31,450 words | source: English | session: dtr_1748000000

Step 5: Glossary
   ✅ 18 terms (API, pipeline, deployment, REST, ...)

Step 6: Translation
   ✅ Chunk 1/12  ✅ Chunk 2/12  ...  ✅ Chunk 12/12

Step 7: Merge
   ✅ 12 chunks merged → ~/Documents/technical_manual.md

Step 8: Cleanup
   ✅ /tmp files removed

Step 9: Done!
   ✅ 12 chunks | 31,450 words | English → Italian | medium | 18 terms
   📄 ~/Documents/technical_manual.md
```
