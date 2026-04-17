#!/usr/bin/env python3
"""
chunk_doc.py — helper for the doc-translator Copilot CLI skill

Commands:
  split        Clean and split the source document into chunks (stored persistently)
  next-batch   Return the next N untranslated chunk paths + contents for the agent to translate
  save-chunk   Save a translated chunk and update session progress
  merge        Concatenate all translated chunks into the final output file
  status       Show translation progress for a session
  list         List all saved sessions
  update-glossary  Add/update terms in the session glossary
  load-state   Print the full state of a session as JSON

Session data is stored permanently in:
  ~/.copilot/doc-translator/sessions/<session_id>/
    state.json          — session metadata + glossary
    src_chunk_NNN.txt   — source chunks
    trl_chunk_NNN.md    — translated chunks
"""

import argparse
import glob as glob_module
import json
import os
import re
import sys
import time
from pathlib import Path

SESSION_ROOT = Path.home() / '.copilot' / 'doc-translator' / 'sessions'


# ─── CLEANUP ────────────────────────────────────────────────────────────────

def cleanup_light(text: str) -> str:
    text = re.sub(r'(\w)-\n(\w)', r'\1\2', text)
    text = re.sub(r'(?<!\S)\n[ \t]*\d{1,4}[ \t]*\n(?!\S)', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'[ \t]+$', '', text, flags=re.MULTILINE)
    return text.strip()


def cleanup_medium(text: str) -> str:
    text = cleanup_light(text)
    text = re.sub(r'^#{1,6}[ \t]*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'^(#{1,6})([^ #\n])', r'\1 \2', text, flags=re.MULTILINE)
    text = re.sub(r'^[ \t]{2,}[-*+][ \t]+', '- ', text, flags=re.MULTILINE)
    text = re.sub(r'^[ \t]*[*+][ \t]+', '- ', text, flags=re.MULTILINE)
    lines = text.split('\n')
    line_counts: dict = {}
    for line in lines:
        stripped = line.strip()
        if stripped:
            line_counts[stripped] = line_counts.get(stripped, 0) + 1
    cleaned = [
        line for line in lines
        if not line.strip() or line_counts.get(line.strip(), 0) <= 3
    ]
    text = '\n'.join(cleaned)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def cleanup_aggressive(text: str) -> str:
    text = cleanup_medium(text)
    text = text.replace('\u00ad', '')
    text = re.sub(r'[\u200b\u200c\u200d\ufeff]', '', text)
    text = text.replace('\ufffd', '')
    lines = text.split('\n')
    merged: list = []
    i = 0
    in_code_block = False
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith('```'):
            in_code_block = not in_code_block
            merged.append(line)
            i += 1
            continue
        if in_code_block:
            merged.append(line)
            i += 1
            continue
        if (not stripped or
                stripped.startswith('#') or
                re.match(r'^[-*+\d]', stripped) or
                re.search(r'[.!?;:»"\']\s*$', stripped)):
            merged.append(line)
            i += 1
            continue
        if (i + 1 < len(lines)
                and len(stripped) < 60
                and lines[i + 1].strip()
                and not lines[i + 1].strip().startswith('#')
                and not re.match(r'^[-*+\d]', lines[i + 1].strip())):
            merged.append(line.rstrip() + ' ' + lines[i + 1].lstrip())
            i += 2
        else:
            merged.append(line)
            i += 1
    text = '\n'.join(merged)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


CLEANUP_FUNCS = {
    'light': cleanup_light,
    'medium': cleanup_medium,
    'aggressive': cleanup_aggressive,
}


# ─── CHUNKING ────────────────────────────────────────────────────────────────

def split_into_chunks(text: str, max_words: int = 2800) -> list:
    """Split text into <=max_words chunks at natural boundaries."""

    def _sentences(blob: str) -> list:
        return re.split(r'(?<=[.!?])\s+', blob)

    def _split_blob(blob: str, mw: int) -> list:
        segs = re.split(r'\n{2,}', blob)
        if len(segs) == 1:
            segs = blob.split('\n')
        if len(segs) == 1:
            segs = _sentences(blob)

        result: list = []
        parts: list = []
        words = 0
        for seg in segs:
            pw = len(seg.split())
            if pw == 0:
                continue
            if pw > mw:
                if parts:
                    result.append('\n'.join(parts).strip())
                    parts = []
                    words = 0
                for sent in _sentences(seg):
                    sw = len(sent.split())
                    if words + sw > mw and parts:
                        result.append(' '.join(parts).strip())
                        parts = [sent]
                        words = sw
                    else:
                        parts.append(sent)
                        words += sw
            elif words + pw > mw and parts:
                result.append('\n'.join(parts).strip())
                parts = [seg]
                words = pw
            else:
                parts.append(seg)
                words += pw
        if parts:
            result.append('\n'.join(parts).strip())
        return [c for c in result if c.strip()]

    has_headings = bool(re.search(r'^#{1,6} ', text, flags=re.MULTILINE))

    if not has_headings:
        return _split_blob(text, max_words)

    sections = re.split(r'(?=^# )', text, flags=re.MULTILINE)
    chunks: list = []
    current_parts: list = []
    current_words = 0

    def flush():
        nonlocal current_parts, current_words
        if current_parts:
            chunks.append('\n\n'.join(current_parts).strip())
            current_parts = []
            current_words = 0

    for section in sections:
        if not section.strip():
            continue
        section_words = len(section.split())
        if section_words > max_words:
            flush()
            for sub in _split_blob(section, max_words):
                chunks.append(sub)
        elif current_words + section_words > max_words:
            flush()
            current_parts.append(section)
            current_words = section_words
        else:
            current_parts.append(section)
            current_words += section_words

    flush()
    return [c for c in chunks if c.strip()]


# ─── LANGUAGE HINT ───────────────────────────────────────────────────────────

def detect_lang_hint(text: str) -> str:
    sample = text[:3000].lower()
    scores = {
        'italian':     len(re.findall(r'\b(il|la|le|gli|del|della|che|non|con|per|una|sono|questo|nella)\b', sample)),
        'english':     len(re.findall(r'\b(the|and|is|in|of|to|a|an|that|it|with|this|are|was)\b', sample)),
        'french':      len(re.findall(r'\b(le|la|les|des|du|un|une|est|dans|qui|sur|pas|par|avec)\b', sample)),
        'spanish':     len(re.findall(r'\b(el|la|los|las|del|en|que|con|por|una|es|se|lo|su)\b', sample)),
        'german':      len(re.findall(r'\b(der|die|das|den|dem|und|ist|in|von|zu|mit|auf|nicht|für)\b', sample)),
        'portuguese':  len(re.findall(r'\b(o|a|os|as|do|da|de|em|que|com|uma|para|por|seu)\b', sample)),
    }
    best_score = max(scores.values())
    if best_score < 5:
        return 'unknown'
    return max(scores, key=scores.get)


# ─── SESSION STATE ───────────────────────────────────────────────────────────

def _session_dir(session_id: str) -> Path:
    d = SESSION_ROOT / session_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _state_path(session_id: str) -> Path:
    return SESSION_ROOT / session_id / 'state.json'


def _load_state(session_id: str) -> dict:
    p = _state_path(session_id)
    if not p.exists():
        _fail(f"Session '{session_id}' not found. Run 'split' first.")
    with open(p) as f:
        return json.load(f)


def _save_state(state: dict) -> None:
    p = _state_path(state['session_id'])
    state['updated_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    with open(p, 'w') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _progress(session_id: str) -> tuple:
    """Returns (done_set, total_count)"""
    sdir = SESSION_ROOT / session_id
    src_files = sorted(sdir.glob('src_chunk_*.txt'))
    total = len(src_files)
    done = set()
    for sf in src_files:
        m = re.search(r'src_chunk_(\d+)\.txt$', sf.name)
        if m:
            n = int(m.group(1))
            trl = sdir / f'trl_chunk_{n:03d}.md'
            if trl.exists() and trl.stat().st_size > 0:
                done.add(n)
    return done, total


# ─── COMMANDS ────────────────────────────────────────────────────────────────

def cmd_split(args: argparse.Namespace) -> None:
    file_path = Path(args.file).expanduser().resolve()
    if not file_path.exists():
        _fail(f"File not found: {file_path}")

    text = file_path.read_text(encoding='utf-8', errors='replace')
    cleanup_fn = CLEANUP_FUNCS.get(args.level, cleanup_medium)
    text = cleanup_fn(text)
    lang_hint = detect_lang_hint(text)

    session_id = args.session or f"dtr_{int(time.time())}"
    sdir = _session_dir(session_id)
    max_words = args.words

    chunks = split_into_chunks(text, max_words=max_words)

    chunk_paths: list = []
    for i, chunk in enumerate(chunks, 1):
        chunk_file = sdir / f'src_chunk_{i:03d}.txt'
        chunk_file.write_text(chunk, encoding='utf-8')
        chunk_paths.append(str(chunk_file))

    total_words = sum(len(c.split()) for c in chunks)

    # Compute output path
    src_stem = file_path.stem
    if file_path.suffix.lower() == '.md':
        out_name = f"{src_stem}_translated.md"
    else:
        out_name = f"{src_stem}.md"
    output_path = str(file_path.parent / out_name)

    state = {
        "session_id": session_id,
        "source_file": str(file_path),
        "output_path": output_path,
        "target_lang": args.target_lang or "",
        "cleanup_level": args.level,
        "chunk_count": len(chunks),
        "total_words": total_words,
        "source_lang": lang_hint,
        "glossary": {},
        "batch_size": args.batch_size,
        "created_at": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        "updated_at": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    }
    _save_state(state)

    _ok({
        "session": session_id,
        "session_dir": str(sdir),
        "chunk_count": len(chunks),
        "total_words": total_words,
        "source_lang": lang_hint,
        "output_path": output_path,
        "level": args.level,
        "source_file": str(file_path),
    })


def cmd_next_batch(args: argparse.Namespace) -> None:
    state = _load_state(args.session)
    done, total = _progress(args.session)
    sdir = SESSION_ROOT / args.session
    batch_size = args.batch_size or state.get('batch_size', 8)

    batch: list = []
    for i in range(1, total + 1):
        if i not in done:
            src = sdir / f'src_chunk_{i:03d}.txt'
            if src.exists():
                content = src.read_text(encoding='utf-8')
                batch.append({
                    "chunk_number": i,
                    "src_path": str(src),
                    "trl_path": str(sdir / f'trl_chunk_{i:03d}.md'),
                    "word_count": len(content.split()),
                    "content": content,
                })
                if len(batch) >= batch_size:
                    break

    remaining_after = total - len(done) - len(batch)

    _ok({
        "session": args.session,
        "target_lang": state.get('target_lang', ''),
        "source_lang": state.get('source_lang', ''),
        "glossary": state.get('glossary', {}),
        "done": len(done),
        "total": total,
        "batch": batch,
        "remaining_after_batch": remaining_after,
        "is_complete": remaining_after == 0 and len(batch) == 0,
    })


def cmd_save_chunk(args: argparse.Namespace) -> None:
    state = _load_state(args.session)
    sdir = SESSION_ROOT / args.session
    n = args.chunk

    # Read translated content from stdin or file
    if args.file:
        content = Path(args.file).read_text(encoding='utf-8')
    else:
        content = sys.stdin.read()

    trl_path = sdir / f'trl_chunk_{n:03d}.md'
    trl_path.write_text(content.strip(), encoding='utf-8')

    done, total = _progress(args.session)
    _ok({
        "saved": str(trl_path),
        "chunk": n,
        "done": len(done),
        "total": total,
        "remaining": total - len(done),
    })


def cmd_update_glossary(args: argparse.Namespace) -> None:
    state = _load_state(args.session)
    new_terms = json.loads(args.terms)
    state.setdefault('glossary', {}).update(new_terms)
    _save_state(state)
    _ok({"glossary": state['glossary'], "terms_count": len(state['glossary'])})


def cmd_merge(args: argparse.Namespace) -> None:
    state = _load_state(args.session)
    sdir = SESSION_ROOT / args.session
    output_path = Path(args.output or state['output_path']).expanduser().resolve()

    trl_files = sorted(sdir.glob('trl_chunk_*.md'))
    if not trl_files:
        _fail(f"No translated chunks found for session '{args.session}'")

    done, total = _progress(args.session)
    if len(done) < total:
        print(json.dumps({
            "warning": f"Only {len(done)}/{total} chunks translated. "
                       f"Run next-batch to complete before merging.",
            "done": len(done),
            "total": total,
        }, ensure_ascii=False, indent=2))

    parts: list = []
    for f in trl_files:
        content = f.read_text(encoding='utf-8').strip()
        if content:
            parts.append(content)

    merged = '\n\n---\n\n'.join(parts)
    merged = re.sub(r'\n{3,}', '\n\n', merged)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(merged, encoding='utf-8')

    _ok({
        "output_path": str(output_path),
        "chunks_merged": len(trl_files),
        "done": len(done),
        "total": total,
    })


def cmd_status(args: argparse.Namespace) -> None:
    state = _load_state(args.session)
    done, total = _progress(args.session)
    sdir = SESSION_ROOT / args.session

    status_list: list = []
    for i in range(1, total + 1):
        src = sdir / f'src_chunk_{i:03d}.txt'
        trl = sdir / f'trl_chunk_{i:03d}.md'
        status_list.append({
            "chunk": i,
            "done": i in done,
            "src_exists": src.exists(),
            "trl_exists": trl.exists(),
        })

    print(json.dumps({
        "session": args.session,
        "source_file": state.get('source_file'),
        "output_path": state.get('output_path'),
        "target_lang": state.get('target_lang'),
        "source_lang": state.get('source_lang'),
        "total_chunks": total,
        "done": len(done),
        "remaining": total - len(done),
        "percent": round(len(done) / total * 100, 1) if total else 0,
        "is_complete": len(done) == total,
        "chunks": status_list,
    }, ensure_ascii=False, indent=2))


def cmd_list(args: argparse.Namespace) -> None:
    if not SESSION_ROOT.exists():
        _ok({"sessions": []})
        return
    sessions: list = []
    for state_file in SESSION_ROOT.glob('*/state.json'):
        try:
            with open(state_file) as f:
                s = json.load(f)
            sid = s['session_id']
            done, total = _progress(sid)
            sessions.append({
                "session_id": sid,
                "source_file": s.get('source_file', ''),
                "target_lang": s.get('target_lang', ''),
                "done": len(done),
                "total": total,
                "percent": round(len(done) / total * 100, 1) if total else 0,
                "is_complete": len(done) == total,
                "created_at": s.get('created_at', ''),
                "updated_at": s.get('updated_at', ''),
            })
        except Exception:
            pass
    sessions.sort(key=lambda x: x.get('updated_at', ''), reverse=True)
    _ok({"sessions": sessions})


def cmd_load_state(args: argparse.Namespace) -> None:
    state = _load_state(args.session)
    done, total = _progress(args.session)
    state['_progress'] = {"done": len(done), "total": total,
                          "percent": round(len(done) / total * 100, 1) if total else 0}
    print(json.dumps(state, ensure_ascii=False, indent=2))


# ─── HELPERS ─────────────────────────────────────────────────────────────────

def _ok(data: dict) -> None:
    print(json.dumps({"success": True, **data}, ensure_ascii=False, indent=2))


def _fail(msg: str) -> None:
    print(json.dumps({"success": False, "error": msg}, ensure_ascii=False))
    sys.exit(1)


# ─── MAIN ────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description='chunk_doc.py — helper for the doc-translator Copilot CLI skill'
    )
    sub = parser.add_subparsers(dest='command', metavar='<command>')

    # split
    p_split = sub.add_parser('split', help='Clean and split document into chunks')
    p_split.add_argument('--file', required=True)
    p_split.add_argument('--level', choices=['light', 'medium', 'aggressive'], default='medium')
    p_split.add_argument('--words', type=int, default=2800)
    p_split.add_argument('--session', default=None)
    p_split.add_argument('--target-lang', default=None)
    p_split.add_argument('--batch-size', type=int, default=8)

    # next-batch
    p_nb = sub.add_parser('next-batch', help='Get next N untranslated chunks for agent to translate')
    p_nb.add_argument('--session', required=True)
    p_nb.add_argument('--batch-size', type=int, default=None)

    # save-chunk
    p_sc = sub.add_parser('save-chunk', help='Save a translated chunk')
    p_sc.add_argument('--session', required=True)
    p_sc.add_argument('--chunk', required=True, type=int)
    p_sc.add_argument('--file', default=None, help='File with translated content (default: stdin)')

    # update-glossary
    p_ug = sub.add_parser('update-glossary', help='Add/update glossary terms')
    p_ug.add_argument('--session', required=True)
    p_ug.add_argument('--terms', required=True, help='JSON object of {term: translation}')

    # merge
    p_merge = sub.add_parser('merge', help='Merge all translated chunks into final output')
    p_merge.add_argument('--session', required=True)
    p_merge.add_argument('--output', default=None)

    # status
    p_status = sub.add_parser('status', help='Show translation progress')
    p_status.add_argument('--session', required=True)

    # list
    sub.add_parser('list', help='List all saved sessions')

    # load-state
    p_ls = sub.add_parser('load-state', help='Print full session state')
    p_ls.add_argument('--session', required=True)

    args = parser.parse_args()

    dispatch = {
        'split': cmd_split,
        'next-batch': cmd_next_batch,
        'save-chunk': cmd_save_chunk,
        'update-glossary': cmd_update_glossary,
        'merge': cmd_merge,
        'status': cmd_status,
        'list': cmd_list,
        'load-state': cmd_load_state,
    }

    if args.command in dispatch:
        dispatch[args.command](args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()
