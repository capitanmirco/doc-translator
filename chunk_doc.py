#!/usr/bin/env python3
"""
chunk_doc.py — helper per la skill doc-translator di Copilot CLI

Uso:
  python3 chunk_doc.py split  --file <path> --level <light|medium|aggressive> [--words N] [--session ID]
  python3 chunk_doc.py merge  --session <ID> --output <path>
  python3 chunk_doc.py status --session <ID>
"""

import argparse
import glob as glob_module
import json
import os
import re
import sys
import time
from pathlib import Path


# ─── CLEANUP ────────────────────────────────────────────────────────────────

def cleanup_light(text: str) -> str:
    """Artefatti minimi: sillabazione spezzata, numeri pagina, a capo in eccesso."""
    # Sillabazione spezzata a fine riga (word-\nword → wordword)
    text = re.sub(r'(\w)-\n(\w)', r'\1\2', text)
    # Numeri di pagina isolati (solo cifre su riga propria)
    text = re.sub(r'(?<!\S)\n[ \t]*\d{1,4}[ \t]*\n(?!\S)', '\n', text)
    # Riduzione eccesso di righe vuote (>2 → 2)
    text = re.sub(r'\n{3,}', '\n\n', text)
    # Spazi/tab finali per riga
    text = re.sub(r'[ \t]+$', '', text, flags=re.MULTILINE)
    return text.strip()


def cleanup_medium(text: str) -> str:
    """Strutturale: light + heading vuoti, liste, header/footer ripetuti."""
    text = cleanup_light(text)

    # Heading vuoti o con soli spazi
    text = re.sub(r'^#{1,6}[ \t]*$', '', text, flags=re.MULTILINE)
    # Assicura spazio dopo # nei heading
    text = re.sub(r'^(#{1,6})([^ #\n])', r'\1 \2', text, flags=re.MULTILINE)

    # Normalizza bullet con rientri anomali → lista piatta
    text = re.sub(r'^[ \t]{2,}[-*+][ \t]+', '- ', text, flags=re.MULTILINE)
    text = re.sub(r'^[ \t]*[*+][ \t]+', '- ', text, flags=re.MULTILINE)

    # Rimuovi righe che compaiono più di 3 volte (probabile header/footer di pagina)
    lines = text.split('\n')
    line_counts: dict[str, int] = {}
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
    """Aggressivo: medium + OCR, soft-hyphen, caratteri anomali, righe spezzate."""
    text = cleanup_medium(text)

    # Soft hyphen e caratteri invisibili
    text = text.replace('\u00ad', '')
    text = re.sub(r'[\u200b\u200c\u200d\ufeff]', '', text)
    # Carattere di sostituzione Unicode (OCR)
    text = text.replace('\ufffd', '')

    # Unisci righe brevi spezzate (< 60 char, non terminanti con punteggiatura forte)
    # Non toccare heading, bullet, righe vuote, code block
    lines = text.split('\n')
    merged: list[str] = []
    i = 0
    in_code_block = False
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Traccia apertura/chiusura code block
        if stripped.startswith('```'):
            in_code_block = not in_code_block
            merged.append(line)
            i += 1
            continue

        if in_code_block:
            merged.append(line)
            i += 1
            continue

        # Non toccare heading, bullet, righe vuote, fine frase
        if (not stripped or
                stripped.startswith('#') or
                re.match(r'^[-*+\d]', stripped) or
                re.search(r'[.!?;:»"\']\s*$', stripped)):
            merged.append(line)
            i += 1
            continue

        # Unisci con la riga successiva se entrambe brevi e la prossima non è heading/bullet
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

def split_into_chunks(text: str, max_words: int = 2800) -> list[str]:
    """
    Divide il testo in chunk di al massimo max_words parole.
    Divide preferibilmente ai confini di sezione (# heading) o di paragrafo (\n\n).
    """
    # Prima suddivisione per heading di primo livello
    sections = re.split(r'(?=^# )', text, flags=re.MULTILINE)

    chunks: list[str] = []
    current_parts: list[str] = []
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
            # Sezione grande: spezza per paragrafi
            flush()
            paragraphs = re.split(r'\n{2,}', section)
            para_parts: list[str] = []
            para_words = 0
            for para in paragraphs:
                pw = len(para.split())
                if para_words + pw > max_words and para_parts:
                    chunks.append('\n\n'.join(para_parts).strip())
                    para_parts = [para]
                    para_words = pw
                else:
                    para_parts.append(para)
                    para_words += pw
            if para_parts:
                chunks.append('\n\n'.join(para_parts).strip())
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
    """Euristica leggera per dedurre la lingua sorgente (non sostituisce NLP)."""
    sample = text[:3000].lower()
    scores = {
        'italiano':    len(re.findall(r'\b(il|la|le|gli|del|della|che|non|con|per|una|sono|questo|nella)\b', sample)),
        'inglese':     len(re.findall(r'\b(the|and|is|in|of|to|a|an|that|it|with|this|are|was)\b', sample)),
        'francese':    len(re.findall(r'\b(le|la|les|des|du|un|une|est|dans|qui|sur|pas|par|avec)\b', sample)),
        'spagnolo':    len(re.findall(r'\b(el|la|los|las|del|en|que|con|por|una|es|se|lo|su)\b', sample)),
        'tedesco':     len(re.findall(r'\b(der|die|das|den|dem|und|ist|in|von|zu|mit|auf|nicht|für)\b', sample)),
        'portoghese':  len(re.findall(r'\b(o|a|os|as|do|da|de|em|que|com|uma|para|por|seu)\b', sample)),
    }
    best_score = max(scores.values())
    if best_score < 5:
        return 'sconosciuta'
    return max(scores, key=scores.get)


# ─── SPLIT ───────────────────────────────────────────────────────────────────

def cmd_split(args: argparse.Namespace) -> None:
    file_path = Path(args.file).expanduser().resolve()
    if not file_path.exists():
        _fail(f"File non trovato: {file_path}")

    text = file_path.read_text(encoding='utf-8', errors='replace')

    cleanup_fn = CLEANUP_FUNCS.get(args.level, cleanup_medium)
    text = cleanup_fn(text)

    lang_hint = detect_lang_hint(text)

    session_id = args.session or f"dtr_{int(time.time())}"
    max_words = args.words

    chunks = split_into_chunks(text, max_words=max_words)

    chunk_paths: list[str] = []
    for i, chunk in enumerate(chunks, 1):
        chunk_file = Path(f'/tmp/doc_trans_{session_id}_src_chunk_{i:03d}.txt')
        chunk_file.write_text(chunk, encoding='utf-8')
        chunk_paths.append(str(chunk_file))

    total_words = sum(len(c.split()) for c in chunks)

    _ok({
        "session": session_id,
        "chunks": chunk_paths,
        "chunk_count": len(chunks),
        "total_words": total_words,
        "lang_hint": lang_hint,
        "level": args.level,
        "source_file": str(file_path),
    })


# ─── MERGE ───────────────────────────────────────────────────────────────────

def cmd_merge(args: argparse.Namespace) -> None:
    session_id = args.session
    output_path = Path(args.output).expanduser().resolve()

    pattern = f'/tmp/doc_trans_{session_id}_trl_chunk_*.md'
    trl_files = sorted(glob_module.glob(pattern))

    if not trl_files:
        _fail(f"Nessun file tradotto trovato per session '{session_id}'. "
              f"Cerca: {pattern}")

    parts: list[str] = []
    for f in trl_files:
        content = Path(f).read_text(encoding='utf-8').strip()
        if content:
            parts.append(content)

    merged = '\n\n'.join(parts)
    merged = re.sub(r'\n{3,}', '\n\n', merged)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(merged, encoding='utf-8')

    _ok({
        "output_path": str(output_path),
        "chunks_merged": len(trl_files),
    })


# ─── STATUS ──────────────────────────────────────────────────────────────────

def cmd_status(args: argparse.Namespace) -> None:
    session_id = args.session
    src_files = sorted(glob_module.glob(f'/tmp/doc_trans_{session_id}_src_chunk_*.txt'))
    trl_set = set(glob_module.glob(f'/tmp/doc_trans_{session_id}_trl_chunk_*.md'))

    status_list: list[dict] = []
    for sf in src_files:
        m = re.search(r'_src_chunk_(\d+)\.txt$', sf)
        if m:
            n = m.group(1)
            trl_path = f'/tmp/doc_trans_{session_id}_trl_chunk_{n}.md'
            status_list.append({
                "chunk": int(n),
                "source": sf,
                "translated_file": trl_path if trl_path in trl_set else None,
                "done": trl_path in trl_set,
            })

    done = sum(1 for s in status_list if s['done'])
    print(json.dumps({
        "session": session_id,
        "total_chunks": len(status_list),
        "done": done,
        "remaining": len(status_list) - done,
        "chunks": status_list,
    }, ensure_ascii=False, indent=2))


# ─── HELPERS ─────────────────────────────────────────────────────────────────

def _ok(data: dict) -> None:
    print(json.dumps({"success": True, **data}, ensure_ascii=False, indent=2))


def _fail(msg: str) -> None:
    print(json.dumps({"success": False, "error": msg}, ensure_ascii=False))
    sys.exit(1)


# ─── MAIN ────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description='chunk_doc.py — helper per la skill doc-translator'
    )
    sub = parser.add_subparsers(dest='command', metavar='<comando>')

    # split
    p_split = sub.add_parser('split', help='Pulisci e dividi il documento in chunk')
    p_split.add_argument('--file', required=True, help='Percorso del file .md o .txt')
    p_split.add_argument('--level', choices=['light', 'medium', 'aggressive'],
                         default='medium', help='Livello di pulizia artefatti')
    p_split.add_argument('--words', type=int, default=2800,
                         help='Parole massime per chunk (default: 2800)')
    p_split.add_argument('--session', default=None,
                         help='ID sessione (generato automaticamente se omesso)')

    # merge
    p_merge = sub.add_parser('merge', help='Unisci i chunk tradotti in un unico file')
    p_merge.add_argument('--session', required=True, help='ID sessione')
    p_merge.add_argument('--output', required=True, help='Percorso file di output finale')

    # status
    p_status = sub.add_parser('status', help='Mostra lo stato di avanzamento della traduzione')
    p_status.add_argument('--session', required=True, help='ID sessione')

    args = parser.parse_args()

    if args.command == 'split':
        cmd_split(args)
    elif args.command == 'merge':
        cmd_merge(args)
    elif args.command == 'status':
        cmd_status(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()
