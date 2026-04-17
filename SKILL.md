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

> 📌 **Step 4/9 — Cleaning artifacts and splitting document into chunks...**

```bash
python3 ~/.copilot/skills/doc-translator/chunk_doc.py split \
  --file /path/to/converted_file.md \
  --level <cleanup_level> \
  --words 2800
```

> Use `--words 2000` for highly expansive language pairs (e.g., German text → other language)
> or if a chunk translation gets truncated in Step 6.

Read the JSON output. Save `SESSION_ID`, `CHUNK_COUNT`, `TOTAL_WORDS`, `LANG_HINT` in memory.

Tell the user:
```
✅ Document split into N chunks (total: X words).
🌍 Detected source language: <lang_hint>
🔑 Session ID: <session_id>  ← save this to resume if the process is interrupted
```

---

## Step 5 — Extract initial glossary

> 📌 **Step 5/9 — Extracting terminology glossary from chunk 1...**

```bash
cat /tmp/doc_trans_<SESSION_ID>_src_chunk_001.txt
```

Analyze the text and build a **glossary** of terms to handle consistently throughout all chunks.
Include:
- Proper names: people, places, organizations, products, brands
- Technical terms, acronyms, abbreviations
- Titles of works, laws, standards, regulations
- Terms that must remain in the source language (international tech terms, file names, URLs, shell commands)
- Terms with a domain-specific translation preference

**Internal glossary format** (keep this updated in memory throughout the entire process):
```
GLOSSARY:
- "Abstract Syntax Tree" → keep as-is (technical term)
- "pipeline" → keep as-is (common English technical term used internationally)
- "Directive 2001/29/EC" → keep as-is (regulatory reference)
- "Mario Rossi" → keep as-is (proper name)
- "deployment" → translate as: "distribuzione" (if target is Italian)
```

Tell the user:
```
✅ Glossary initialized with N terms.
```

---

## Step 6 — Translate chunk by chunk

> 📌 **Step 6/9 — Starting translation (N chunks total)...**

Repeat for each chunk from **001** to **NNN**, in order:

### 6a. Check for already-translated chunks (resume support)

```bash
python3 ~/.copilot/skills/doc-translator/chunk_doc.py status --session <SESSION_ID>
```

For each chunk with `"done": true` → skip it:
```
⏭️  Chunk N/TOTAL: already translated — skipping.
```

### 6b. Read the source chunk

```bash
cat /tmp/doc_trans_<SESSION_ID>_src_chunk_NNN.txt
```

Announce before translating:
```
⏳ Translating chunk N/TOTAL (~X words)...
```

### 6c. Translate the chunk

Use this internal prompt (substitute all placeholders before sending):

---
**TRANSLATION PROMPT** (applied internally for each chunk):

```
You are a professional translator specializing in [DOCUMENT DOMAIN, if detectable from context].
Translate the following text into [TARGET_LANGUAGE].

RULES:
1. Preserve ALL Markdown formatting: headings (#, ##, ###), lists (-, *), bold (**),
   italic (*), code (`inline` and ```blocks```), tables (|), links ([text](url)), blockquotes (>)
2. Do NOT translate: source code, shell commands, URLs, file paths, variables, file names
3. Do NOT translate terms marked "keep as-is" in the glossary below
4. Use natural, fluent [TARGET_LANGUAGE] — avoid literal word-for-word translation
5. Maintain paragraph structure and approximate length
6. Cleanup level active: [LEVEL] — if you spot remaining artifacts (isolated numbers, truncated
   lines, anomalous characters, broken words), fix them silently during translation
7. After the translated text, if you encounter new technical terms or proper names not in the
   glossary, list them under "NEW_TERMS:" (one per line, format: "term" → "how to handle it")

CURRENT GLOSSARY (follow these choices consistently throughout):
[GLOSSARY]

---START OF TEXT TO TRANSLATE---
[CHUNK_CONTENT]
---END OF TEXT---

Output format:
1. The complete translated text in valid Markdown
2. Optionally: a "NEW_TERMS:" section at the very end with glossary additions
Do NOT add any introduction, preamble, explanation, or comment outside these two sections.
```

---

### 6d. Save the translated chunk as an intermediate file

Write **only the translated text** (exclude the `NEW_TERMS:` section) to:

```
/tmp/doc_trans_<SESSION_ID>_trl_chunk_NNN.md
```

```bash
cat > /tmp/doc_trans_<SESSION_ID>_trl_chunk_NNN.md << 'CHUNK_END'
[TRANSLATED TEXT HERE]
CHUNK_END
```

### 6e. Update the glossary

If the translation response includes a `NEW_TERMS:` section, add those terms to the internal glossary.
This ensures consistency for all remaining chunks.

### 6f. Report progress

```
✅ Chunk N/TOTAL translated — saved to /tmp/doc_trans_<SESSION_ID>_trl_chunk_NNN.md
```

---

## Step 7 — Merge into final output

> 📌 **Step 7/9 — Merging all translated chunks into the final file...**

```bash
python3 ~/.copilot/skills/doc-translator/chunk_doc.py merge \
  --session <SESSION_ID> \
  --output <OUTPUT_PATH>
```

If `success: false` → read `"error"` and verify all `_trl_chunk_*.md` files exist and are non-empty.

Tell the user:
```
✅ Merged N chunks → <OUTPUT_PATH>
```

---

## Step 8 — Clean up temporary files

> 📌 **Step 8/9 — Removing temporary files...**

```bash
rm -f /tmp/doc_trans_<SESSION_ID>_src_chunk_*.txt
rm -f /tmp/doc_trans_<SESSION_ID>_trl_chunk_*.md
```

```
✅ Temporary files removed.
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
| `merge: No files found` | `/tmp` was cleared or wrong session ID | Cannot recover — re-run split + translation from scratch |
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
