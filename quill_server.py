#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Quill → Obsidian webhook server (enhanced version with Python extension benefits)
- Summary webhook saves Summary.md and a .pending.json
- Auto/Manual reconcile reads local Quill SQLite DB to find matching meeting + transcript
- Transcript rendering groups consecutive blocks and labels speakers robustly
- Single-anchor diarization: exactly ONE speaker_id is mapped to the configured canonical name (the local mic/source)
- /debug/diarization_map to inspect speaker_id → final name mapping before writing
- Enhanced with better error handling, validation, and structured logging
"""

from __future__ import annotations

import difflib
import json
import logging
import re
import sqlite3
import sys
import itertools
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, List, Tuple, Dict, Union, Literal, Set

from flask import Flask, request, Response

# Import our enhanced modules
from config import config, CONFIDENCE_THRESHOLDS, WEIGHT_TABLE, SessionType
from validation import (
    validate_meeting_id, validate_date_format, validate_timestamp_ms,
    validate_session_type, validate_participants, sanitize_filename,
    validate_webhook_payload
)
from logging_config import (
    setup_logging, log_meeting_processed, log_webhook_received,
    log_speaker_mapping, log_matching_result
)
from database import (
    get_db_connection, DatabaseError, get_meeting_by_id,
    get_meetings_in_window, get_contact_meeting_speakers,
    check_database_health
)

# ----------------- USER CONFIG -----------------
# Configuration is now managed in config.py
# Access via: config.notes_root, config.quill_db_path, etc.

# Legacy constants for backward compatibility
NOTES_ROOT = config.notes_root
SUMMARIES_ROOT = config.summaries_root
TRANSCRIPTS_ROOT = config.transcripts_root
PENDING_SUFFIX = config.pending_suffix
QUILL_DB_PATH = config.quill_db_path
MY_CANONICAL_NAME = config.canonical_name
MY_ALIASES = config.aliases
PORT = config.port
HOST = config.host
AUTO_RECONCILE_ON_SUMMARY = config.auto_reconcile_on_summary
WINDOW_HOURS = config.window_hours
CONF_THRESHOLDS = CONFIDENCE_THRESHOLDS
STRIP_TOKENS = config.strip_tokens
LOCAL_SOURCES = config.local_sources
ENABLE_SPEAKER_CONSOLIDATION = config.enable_speaker_consolidation
SPEAKER_SIMILARITY_THRESHOLD = config.speaker_similarity_threshold

# --------------- Flask ---------------
app = Flask(__name__)

# Setup enhanced logging
setup_logging(log_level="INFO")
logger = logging.getLogger(__name__)

def ok_json(payload: dict, status: int = 200) -> Response:
    return Response(json.dumps(payload, ensure_ascii=False), status=status, mimetype="application/json")

def err_json(message: str, status: int = 400, extra: dict | None = None) -> Response:
    body = {"ok": False, "error": message}
    if extra:
        body.update(extra)
    return ok_json(body, status=status)

SAFE_CHAR_RE = re.compile(r'[\\/:*?"<>|]')
COLON_FOR_FILENAME = "ː"

def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)

def yyyymm_from_date(date_str: str) -> str:
    dt = datetime.strptime(date_str, "%Y-%m-%d").date()
    return f"{dt.year:04d}-{dt.month:02d}"

def safe_filename(name: Optional[str]) -> str:
    """Safely sanitize filename using validation module."""
    return sanitize_filename(name)

def ms_to_iso(ms: Optional[int]) -> Optional[str]:
    if ms is None:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()
    except Exception:
        return None

def ms_length_min(a: Optional[int], b: Optional[int]) -> Optional[int]:
    if a is None or b is None or b < a:
        return None
    return int((b - a) / 1000 / 60)

# ---------- YAML-ish frontmatter pickers ----------
FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", flags=re.DOTALL)

def parse_frontmatter(markdown: str) -> dict:
    md = markdown or ""
    m = FRONTMATTER_RE.search(md)
    if not m:
        return {}
    yaml_block = m.group(1)

    def pick_scalar(key: str) -> Optional[str]:
        pat = re.compile(rf"^{key}:\s*\"?([^\n\"]+)\"?\s*$", re.MULTILINE)
        mm = pat.search(yaml_block)
        return mm.group(1).strip() if mm else None

    def pick_list(key: str) -> List[str]:
        pat_inline = re.compile(rf"^{key}:\s*(\[[^\]]*\])\s*$", re.MULTILINE)
        mi = pat_inline.search(yaml_block)
        if mi:
            try:
                s = mi.group(1)
                # Handle both single and double quotes, and escape any unescaped quotes
                s_json = re.sub(r"'", '"', s)
                # Remove any trailing commas before closing bracket
                s_json = re.sub(r',\s*]', ']', s_json)
                arr = json.loads(s_json)
                return [str(x).strip() for x in arr if str(x).strip()]
            except Exception as e:
                # If JSON parsing fails, try a more robust approach
                try:
                    s = mi.group(1)
                    # Extract items between quotes, handling both single and double quotes
                    items = re.findall(r'["\']([^"\']+)["\']', s)
                    return [item.strip() for item in items if item.strip()]
                except Exception:
                    pass
        pat_dash = re.compile(rf"^{key}:\s*\n((?:\s*-\s*[^\n]+\n)+)", re.MULTILINE)
        mdash = pat_dash.search(yaml_block)
        if mdash:
            raw = mdash.group(1).strip().splitlines()
            out = []
            for line in raw:
                item = re.sub(r"^\s*-\s*", "", line).strip().strip('"').strip("'")
                if item:
                    out.append(item)
            return out
        return []

    fm = {
        "session_type": pick_scalar("session_type"),
        "participants": pick_list("participants"),
        "tags": pick_list("tags"),
        "project": pick_scalar("project"),
        "source": pick_scalar("source"),
    }
    return {k: v for k, v in fm.items() if v}

# ---------- Transcript helpers ----------
def is_json_like(s: str) -> bool:
    s = (s or "").strip()
    if not s or s[0] not in "{[":
        return False
    try:
        json.loads(s)
        return True
    except Exception:
        return False

def sanitize_text(text: str) -> str:
    out = text or ""
    for token, repl in STRIP_TOKENS.items():
        out = out.replace(token, repl)
    return out.strip()

def group_blocks_by_key(blocks: List[dict], key: str) -> List[Tuple[str, str]]:
    grouped: List[Tuple[str, str]] = []
    cur_key = None
    buf: List[str] = []
    for b in blocks:
        txt = sanitize_text(b.get("text", ""))
        if not txt:
            continue
        k = b.get(key)
        if k == cur_key:
            buf.append(txt)
        else:
            if cur_key is not None and buf:
                grouped.append((str(cur_key), " ".join(buf)))
            cur_key = k
            buf = [txt]
    if cur_key is not None and buf:
        grouped.append((str(cur_key), " ".join(buf)))
    return grouped

def _parse_audio_blocks(audio_blob: str) -> Optional[Tuple[List[dict], Optional[dict]]]:
    if not is_json_like(audio_blob):
        return None, None
    try:
        data = json.loads(audio_blob)
    except Exception:
        return None, None

    meta = None
    if isinstance(data, dict):
        blocks = data.get("blocks") if isinstance(data.get("blocks"), list) else None
        meta = {k: v for k, v in data.items() if k != "blocks"}
        if blocks is not None:
            return blocks, meta
    if isinstance(data, list):
        return data, None
    return None, None

def _clean_display_name(raw: str) -> str:
    s = (raw or "").strip()
    s = re.sub(r"^\s*and\s+", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\([^)]*\)", "", s).strip()
    base = s.lower()
    if base in {a.lower() for a in MY_ALIASES} or base == "me":
        return MY_CANONICAL_NAME
    # Preserve capitalization of canonical name parts
    canonical_parts = MY_CANONICAL_NAME.split()
    canonical_lower = {p.lower() for p in canonical_parts}
    parts = re.split(r"\s+", s)
    fixed = []
    for w in parts:
        if w.lower() in canonical_lower:
            # Find matching part in canonical name to preserve capitalization
            for cp in canonical_parts:
                if cp.lower() == w.lower():
                    fixed.append(cp)
                    break
            else:
                fixed.append(w.capitalize())
        else:
            fixed.append(w.capitalize())
    return " ".join([p for p in fixed if p])

def _participants_list_from_string(participants_field: Optional[str]) -> List[str]:
    if not participants_field:
        return []
    parts = re.split(r",|&|\band\b", participants_field, flags=re.IGNORECASE)
    parts = [_clean_display_name(p) for p in parts if p and p.strip()]
    seen = set()
    out = []
    for p in parts:
        key = p.lower()
        if key not in seen:
            out.append(p)
            seen.add(key)
    return out

def _build_speaker_map_from_speakers_json(speakers_json: Optional[str]) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    if not speakers_json:
        return mapping
    try:
        sj = json.loads(speakers_json)
    except Exception:
        return mapping

    def add(sid_val, name_val):
        if sid_val is None or name_val is None:
            return
        try:
            key = str(int(sid_val))
        except Exception:
            key = str(sid_val)
        mapping[key] = _clean_display_name(str(name_val))

    if isinstance(sj, list):
        for obj in sj:
            if isinstance(obj, dict):
                sid = obj.get("id", obj.get("speaker_id", obj.get("sid")))
                name = obj.get("name", obj.get("display_name", obj.get("label")))
                add(sid, name)
    elif isinstance(sj, dict):
        lst = sj.get("speakers") or sj.get("speaker_labels")
        if isinstance(lst, list):
            for obj in lst:
                if isinstance(obj, dict):
                    sid = obj.get("id", obj.get("speaker_id", obj.get("sid")))
                    name = obj.get("name", obj.get("display_name", obj.get("label")))
                    add(sid, name)
        elif isinstance(lst, dict):
            for sid, name in lst.items():
                add(sid, name)
    return mapping

def _pick_me_speaker_id(blocks: List[dict]) -> Optional[str]:
    """Return the speaker_id that most frequently appears with a 'local' source (mic/local/local-user)."""
    counts: Dict[str, int] = {}
    for b in blocks:
        sid = b.get("speaker_id")
        src = str(b.get("source", "")).lower()
        if sid is None or not src:
            continue
        if src in {"mic", "local", "local-user"}:
            sid_str = str(sid)
            counts[sid_str] = counts.get(sid_str, 0) + 1
    if not counts:
        return None
    return sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[0][0]

def _second_pass_relable(lines: List[Tuple[str, str]], participants: List[str]) -> List[Tuple[str, str]]:
    if len(participants) < 2 or not lines:
        return lines
    uniq = list(dict.fromkeys([lab for lab, _ in lines]))
    if len(uniq) > 1:
        return lines
    p0, p1 = participants[0], participants[1]
    flip = False
    fixed = []
    for lab, txt in lines:
        fixed.append((p0 if not flip else p1, txt))
        flip = not flip
    return fixed

# Validate configuration before proceeding
# Auto-detect path if placeholder is used and we're running from the project directory
if str(NOTES_ROOT) == "/path/to/your/quillsidian/project":
    # Try to auto-detect: use the directory containing this script
    script_dir = Path(__file__).parent.resolve()
    if script_dir.name == "Quillsidian" or (script_dir / "quill_server.py").exists():
        # Update config and all dependent constants
        config.notes_root = script_dir
        NOTES_ROOT = script_dir
        SUMMARIES_ROOT = config.summaries_root
        TRANSCRIPTS_ROOT = config.transcripts_root
    else:
        error_msg = (
            "❌ Configuration Error: Quillsidian is not configured!\n\n"
            "Please either:\n"
            "1. Copy config.example.py to config.py and update notes_root with your actual path, OR\n"
            "2. Set the QUILL_NOTES_ROOT environment variable\n\n"
            f"Current notes_root: {NOTES_ROOT}\n"
            "See README.md for configuration instructions."
        )
        print(error_msg, file=sys.stderr)
        sys.exit(1)
elif not NOTES_ROOT.exists():
    error_msg = (
        "❌ Configuration Error: notes_root path does not exist!\n\n"
        f"Path: {NOTES_ROOT}\n\n"
        "Please update config.py or set QUILL_NOTES_ROOT environment variable.\n"
        "See README.md for configuration instructions."
    )
    print(error_msg, file=sys.stderr)
    sys.exit(1)

OVERRIDES_DIR = NOTES_ROOT / ".quill_overrides"
ensure_dir(OVERRIDES_DIR)

INTRO_PATTERNS = [
    r"\b(?:i am|i'm|this is|speaking is)\s+(?P<name>[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b",
    r"\b(?:my name is)\s+(?P<name>[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b",
]
ADDRESS_PATTERNS = [
    r"\b(?:hi|hey|hello|thanks|thank you|great point|good point|question,)\s+(?P<name>[A-Z][a-z]+)\b",
    r"\b(?P<name>[A-Z][a-z]+),\b",
]

def load_label_override(meeting_id: str) -> dict:
    p = OVERRIDES_DIR / f"{meeting_id}.labels.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def save_label_override(meeting_id: str, mapping: dict) -> None:
    p = OVERRIDES_DIR / f"{meeting_id}.labels.json"
    try:
        p.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

def _first_name(full: str) -> str:
    parts = (full or "").strip().split()
    return parts[0] if parts else ""

def _compile_regexes():
    return [re.compile(p, flags=re.IGNORECASE) for p in INTRO_PATTERNS], [re.compile(p, flags=re.IGNORECASE) for p in ADDRESS_PATTERNS]

INTRO_RES, ADDRESS_RES = _compile_regexes()

def _name_affinity_scores(blocks_by_sid: dict[str, list[str]], candidate_names: list[str]) -> dict[str, dict[str, float]]:
    """
    For each speaker_id, compute affinity to each candidate first-name.
    Higher when the *speaker's own* text includes self-intro patterns for that name.
    Lower when that name appears chiefly in other speakers' text (as address).
    Returns: {sid: {Name: score}}
    """
    # Build per-sid corpora
    first_names = {n: _first_name(n) for n in candidate_names}
    sid_scores: dict[str, dict[str, float]] = {sid: {n: 0.0 for n in candidate_names} for sid in blocks_by_sid.keys()}

    # Precompute total addressed-name counts by other sids
    addressed_elsewhere: dict[str, float] = {n: 0.0 for n in candidate_names}
    for sid, texts in blocks_by_sid.items():
        for t in texts:
            for rex in ADDRESS_RES:
                for m in rex.finditer(t):
                    addr = (m.group("name") or "").strip()
                    for n, fn in first_names.items():
                        if addr.lower() == fn.lower():
                            addressed_elsewhere[n] += 1.0

    # Self-intros per sid
    for sid, texts in blocks_by_sid.items():
        for t in texts:
            # Self-intros heavily weighted
            for rex in INTRO_RES:
                for m in rex.finditer(t):
                    nm = (m.group("name") or "").strip()
                    for n, fn in first_names.items():
                        if nm.lower() == fn.lower():
                            sid_scores[sid][n] += 3.0
            # Light weight for "FirstName speaking" patterns even without explicit intro verb
            for n, fn in first_names.items():
                if re.search(rf"\b{re.escape(fn)}\s+(here|speaking)\b", t, flags=re.IGNORECASE):
                    sid_scores[sid][n] += 1.0

    # Penalize if most mentions of a name happen in other speakers
    total_addr = sum(addressed_elsewhere.values()) or 1.0
    for sid in sid_scores:
        for n in candidate_names:
            penalty = 0.0
            # If name has many addressed mentions overall, subtract a tiny global penalty
            penalty = 0.2 * (addressed_elsewhere[n] / total_addr)
            sid_scores[sid][n] = max(0.0, sid_scores[sid][n] - penalty)

    return sid_scores

def _solve_injective_mapping(sids: list[str], names: list[str], scores: dict[str, dict[str, float]]) -> dict[str, str]:
    """
    Choose a one-to-one sid→name assignment maximizing sum of scores.
    Small n → brute-force over permutations.
    """
    best = {}
    best_score = -1e9
    for perm in itertools.permutations(names, r=min(len(names), len(sids))):
        total = 0.0
        cur = {}
        for sid, nm in zip(sids, perm):
            sc = scores.get(sid, {}).get(nm, 0.0)
            total += sc
            cur[sid] = nm
        if total > best_score:
            best_score = total
            best = cur
    return best

# === Speaker attribution helpers required by _render_transcript_body ===

ME_FULLNAME = MY_CANONICAL_NAME
ME_ALIASES = MY_ALIASES

def _norm_name(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())

def _is_me(name: str) -> bool:
    n = _norm_name(name)
    if not n:
        return False
    if n in ME_ALIASES:
        return True
    # heuristics: check if name tokens match canonical name tokens
    # Check for exact word matches rather than substring matches
    tokens = n.split()
    canonical_tokens = {t.lower() for t in MY_CANONICAL_NAME.split()}
    return any(token.lower() in canonical_tokens for token in tokens)

def _extract_explicit_label(block: dict) -> Optional[str]:
    for k in ("speaker_name", "display_name", "name", "label", "speaker"):
        v = block.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, dict):
            for kk in ("name", "display_name", "label"):
                vv = v.get(kk)
                if isinstance(vv, str) and vv.strip():
                    return vv.strip()
    return None

def _speaker_key(block: dict, idx: int) -> str:
    for k in ("speaker_id", "speakerId", "participant_id", "participantId"):
        if block.get(k) is not None:
            return f"id:{block[k]}"
    if block.get("source"):
        return f"src:{block['source']}"
    if block.get("cluster") is not None:
        return f"clu:{block['cluster']}"
    return f"idx:{idx}"

def _parse_people_from_free_text(s: str) -> List[str]:
    if not s:
        return []
    s2 = re.sub(r"\s+and\s+", ",", s, flags=re.I)
    parts = [p.strip() for p in re.split(r",|\uFF0C|;", s2) if p.strip()]
    out = []
    seen = set()
    for p in parts:
        p = re.sub(r"\s*\([^)]*\)\s*", "", p).strip()
        if _norm_name(p) in {"and", "me", "myself"}:
            p = "me"
        key = _norm_name(p)
        if key and key not in seen:
            seen.add(key)
            out.append(p)
    return out

def _title_people_order(quill_title: Optional[str], meeting_title: Optional[str]) -> List[str]:
    names: List[str] = []
    def split_title(t: str) -> List[str]:
        if not t:
            return []
        lead = re.split(r"\s*[|\-:\u2013\u2014]\s*", t, maxsplit=1)[0]
        lead = re.sub(r"\s*&\s*", " / ", lead)
        return [p.strip() for p in re.split(r"/|,|;", lead) if p.strip()]
    for t in (quill_title or "", meeting_title or ""):
        for n in split_title(t):
            if _norm_name(n) not in {_norm_name(x) for x in names}:
                names.append(n)
    # put me first if present
    me_ix = next((i for i, n in enumerate(names) if _is_me(n)), None)
    if me_ix is not None:
        me = names.pop(me_ix)
        names.insert(0, me)
    return names

def _compute_speaker_similarity(speaker1_blocks: List[dict], speaker2_blocks: List[dict]) -> float:
    """
    Compute similarity between two speakers based on their speech patterns.
    Returns a score between 0 and 1, where higher means more similar.
    """
    if not speaker1_blocks or not speaker2_blocks:
        return 0.0
    
    # Extract text content
    text1 = " ".join([b.get("text", "") for b in speaker1_blocks])
    text2 = " ".join([b.get("text", "") for b in speaker2_blocks])
    
    if not text1.strip() or not text2.strip():
        return 0.0
    
    # Use sequence matcher for text similarity
    similarity = difflib.SequenceMatcher(None, text1.lower(), text2.lower()).ratio()
    
    # Additional heuristics for speaker similarity
    # Check if both speakers have similar source patterns
    sources1 = set(str(b.get("source", "")).lower() for b in speaker1_blocks)
    sources2 = set(str(b.get("source", "")).lower() for b in speaker2_blocks)
    source_similarity = len(sources1 & sources2) / len(sources1 | sources2) if sources1 | sources2 else 0.0
    
    # Check timing patterns (if speakers alternate frequently, they might be the same person)
    # This is a simplified check - could be enhanced with more sophisticated timing analysis
    timing_score = 0.0
    if len(speaker1_blocks) > 1 and len(speaker2_blocks) > 1:
        # Simple check: if speakers appear in close proximity, they might be the same
        all_blocks = sorted(speaker1_blocks + speaker2_blocks, key=lambda x: x.get("start", 0))
        alternations = 0
        for i in range(1, len(all_blocks)):
            prev_speaker = all_blocks[i-1].get("speaker_id")
            curr_speaker = all_blocks[i].get("speaker_id")
            if prev_speaker != curr_speaker:
                alternations += 1
        timing_score = min(1.0, alternations / len(all_blocks))
    
    # Weighted combination of similarity measures
    final_score = (similarity * 0.4 + source_similarity * 0.3 + timing_score * 0.3)
    return final_score

def _consolidate_similar_speakers(blocks: List[dict], similarity_threshold: float = 0.7) -> List[dict]:
    """
    Consolidate blocks from similar speakers before attribution.
    This helps when Quill creates multiple speaker IDs for the same person.
    """
    if not blocks:
        return blocks
    
    # Group blocks by speaker_id
    speaker_groups: Dict[str, List[dict]] = {}
    for block in blocks:
        # Handle case where block might be a string instead of dict
        if isinstance(block, str):
            continue
        speaker_id = block.get("speaker_id")
        if speaker_id:
            if speaker_id not in speaker_groups:
                speaker_groups[speaker_id] = []
            speaker_groups[speaker_id].append(block)
    
    if len(speaker_groups) <= 1:
        return blocks
    
    # For 1:1 meetings, prioritize consolidating non-local speakers
    # First, try to identify the local speaker by looking for local sources
    local_speaker_id = None
    for speaker_id, speaker_blocks in speaker_groups.items():
        for block in speaker_blocks:
            source = str(block.get("source", "")).lower()
            if source in {"mic", "local", "local-user", "me"}:
                local_speaker_id = speaker_id
                break
        if local_speaker_id:
            break
    
    # Find similar speakers, prioritizing consolidation of non-local speakers
    speaker_ids = list(speaker_groups.keys())
    consolidated = set()
    
    # If we have a local speaker, try to consolidate other speakers first
    if local_speaker_id and len(speaker_groups) > 2:
        # Find pairs of non-local speakers to consolidate
        non_local_speakers = [sid for sid in speaker_ids if sid != local_speaker_id]
        
        for i, sid1 in enumerate(non_local_speakers):
            if sid1 in consolidated:
                continue
                
            for sid2 in non_local_speakers[i+1:]:
                if sid2 in consolidated:
                    continue
                    
                similarity = _compute_speaker_similarity(
                    speaker_groups[sid1], 
                    speaker_groups[sid2]
                )
                
                if similarity >= similarity_threshold:
                    # Consolidate sid2 into sid1
                    logger.info(f"Consolidating non-local speakers: {sid2} -> {sid1} (similarity: {similarity:.2f})")
                    for block in speaker_groups[sid2]:
                        block["speaker_id"] = sid1
                    consolidated.add(sid2)
    
    # If no consolidation happened or no local speaker identified, fall back to original logic
    if not consolidated:
        for i, sid1 in enumerate(speaker_ids):
            if sid1 in consolidated:
                continue
                
            for sid2 in speaker_ids[i+1:]:
                if sid2 in consolidated:
                    continue
                    
                similarity = _compute_speaker_similarity(
                    speaker_groups[sid1], 
                    speaker_groups[sid2]
                )
                
                if similarity >= similarity_threshold:
                    # Consolidate sid2 into sid1
                    logger.info(f"Consolidating similar speakers: {sid2} -> {sid1} (similarity: {similarity:.2f})")
                    for block in speaker_groups[sid2]:
                        block["speaker_id"] = sid1
                    consolidated.add(sid2)
    
    return blocks

def _enhance_speaker_attribution_with_context(blocks: List[dict], meeting_title: Optional[str], quill_title: Optional[str], participants: List[str]) -> List[dict]:
    """
    Enhance speaker attribution using meeting context and participant information.
    This helps improve accuracy for 1:1 meetings and known participants.
    """
    if not blocks or len(participants) < 2:
        return blocks
    
    # For 1:1 meetings, we know there should be exactly 2 speakers
    is_one_on_one = any("1:1" in str(t).lower() or "1-on-1" in str(t).lower() 
                        for t in [meeting_title, quill_title] if t)
    
    # Also detect coaching sessions and other 1:1 meetings by participant count
    if not is_one_on_one:
        # Check if this looks like a 1:1 meeting based on participants
        participant_count = len([p for p in participants if p and not _is_me(p)])
        is_one_on_one = participant_count == 1
    
    if is_one_on_one:
        # Get unique speaker IDs
        speaker_ids = set()
        for block in blocks:
            # Handle case where block might be a string instead of dict
            if isinstance(block, str):
                continue
            speaker_id = block.get("speaker_id")
            if speaker_id:
                speaker_ids.add(speaker_id)
        
        # If we have more than 2 speaker IDs, try to consolidate
        if len(speaker_ids) > 2:
            logger.info(f"1:1 meeting detected with {len(speaker_ids)} speaker IDs, attempting consolidation")
            # Use a lower threshold for 1:1 meetings to be more aggressive about consolidation
            blocks = _consolidate_similar_speakers(blocks, similarity_threshold=0.3)
    
    return blocks

# Add just below _title_people_order(...)
def _looks_like_title_fragment(label: str,
                               quill_title: Optional[str],
                               meeting_title: Optional[str]) -> bool:
    """
    True if 'label' is probably a meeting-title fragment (not a person):
    - very high similarity to the title's leading segment
    - or contains angle-bracket separators like 'Stripe<>Automattic'
    """
    if not label:
        return False
    nl = normalize_title(label)
    if "<>" in label:
        return True
    for t in (quill_title or "", meeting_title or ""):
        # Compare to the portion left of ':' (often the "names" or "series" bit)
        lead = t.split(":", 1)[0]
        nt = normalize_title(lead)
        if not nt:
            continue
        # treat prefix or high similarity as "title-y"
        if nl and (nt.startswith(nl) or nl in nt):
            return True
        if difflib.SequenceMatcher(None, nl, nt).ratio() >= 0.78:
            return True
    return False

def _merge_name_sources(
    quill_title: Optional[str],
    meeting_title: Optional[str],
    db_participants: Optional[str],
    desired_participants: Optional[List[str]] = None,
) -> List[str]:
    order: List[str] = []
    seen = set()
    def add_many(seq):
        for x in seq or []:
            key = _norm_name(x)
            if key and key not in seen:
                seen.add(key)
                order.append(x)
    add_many(_title_people_order(quill_title, meeting_title))
    add_many(desired_participants or [])
    add_many(_parse_people_from_free_text(db_participants or ""))
    me_ix = next((i for i, n in enumerate(order) if _is_me(n)), None)
    if me_ix is not None and me_ix != 0:
        me = order.pop(me_ix)
        order.insert(0, me)
    return [n for n in order if n]

def _first_appearance_mapping(blocks: List[dict],
                              pref_names: List[str],
                              overrides: Dict[str, str],
                              quill_title: Optional[str] = None,
                              meeting_title: Optional[str] = None) -> Dict[str, str]:
    """
    Build mapping from speaker_key -> final display name.
    Priority:
      1. explicit block label
      2. overrides pre-seeded by explicit labels (if any)
      3. **ANCHOR 'me' by local/mic diarization cluster (NEW)**
      4. first-appearance non-me -> next non-me in pref_names
      5. unknowns -> generic "Speaker N"
    """
    mapping: Dict[str, str] = {}
    used_names: set = set()

    # --- NEW: anchor "me" to the diarization cluster that most often appears
    # with a local/mic source (if present in this recording)
    me_sid = _pick_me_speaker_id(blocks)
    if me_sid is not None:
        me_key = f"id:{me_sid}"
        mapping[me_key] = ME_FULLNAME
        used_names.add(_norm_name(ME_FULLNAME))
    # --- END NEW

    # Seed from explicit labels
    for idx, b in enumerate(blocks):
        key = _speaker_key(b, idx)
        explicit = _extract_explicit_label(b)
        if explicit and not _looks_like_title_fragment(explicit, quill_title, meeting_title):
            mapping[key] = explicit
            used_names.add(_norm_name(explicit))

    # Merge any saved manual overrides
    for k, v in (overrides or {}).items():
        if k not in mapping and v:
            mapping[k] = v
            used_names.add(_norm_name(v))

    pref_non_me = [n for n in pref_names if not _is_me(n)]

    next_non_me_ix = 0
    for idx, b in enumerate(blocks):
        key = _speaker_key(b, idx)
        if key in mapping:
            continue

        # If this specific block clearly looks like "me", stamp it now
        if _norm_name(b.get("source", "")) in {"me", "local", "local_user", "self"}:
            mapping[key] = ME_FULLNAME
            used_names.add(_norm_name(ME_FULLNAME))
            continue

        # Rare: inline "Name: …" prefix in raw text
        txt = (b.get("text") or "").strip()
        m = re.match(r"^([A-Za-z][A-Za-z .'-]{0,40}?):\s+", txt)
        if m and not _is_me(m.group(1)):
            guessed = m.group(1).strip()
            mapping[key] = guessed
            used_names.add(_norm_name(guessed))
            continue

        # Assign next non-me from preference list
        while next_non_me_ix < len(pref_non_me) and _norm_name(pref_non_me[next_non_me_ix]) in used_names:
            next_non_me_ix += 1
        if next_non_me_ix < len(pref_non_me):
            candidate = pref_non_me[next_non_me_ix]
            mapping[key] = candidate
            used_names.add(_norm_name(candidate))
            next_non_me_ix += 1
        else:
            anon_ix = sum(1 for v in mapping.values() if v.startswith("Speaker "))
            mapping[key] = f"Speaker {anon_ix+1}"

    # Canonicalize any me-ish names
    for k, v in list(mapping.items()):
        if _is_me(v):
            mapping[k] = ME_FULLNAME
    return mapping

def _group_consecutive_blocks(blocks: List[dict]) -> List[Tuple[str, str]]:
    grouped: List[Tuple[str, str]] = []
    last_key = None
    buf: List[str] = []
    for idx, b in enumerate(blocks):
        key = _speaker_key(b, idx)
        txt = (b.get("text") or "").strip()
        if not txt:
            continue
        txt = txt.replace("<SNIP>", "").strip()
        if not txt:
            continue
        if key != last_key and buf:
            grouped.append((last_key, " ".join(buf).strip()))
            buf = []
        buf.append(txt)
        last_key = key
    if buf and last_key is not None:
        grouped.append((last_key, " ".join(buf).strip()))
    return grouped

# --- BEGIN PATCH: drop-in replacement for render_transcript_markdown ---

def _render_transcript_body(
    blocks: List[dict],
    *,
    meeting_title: Optional[str] = None,
    quill_meeting_id: Optional[str] = None,
    quill_title: Optional[str] = None,
    db_participants_list: Optional[List[str]] = None,
    desired_participants: Optional[List[str]] = None,
    stored_overrides: Optional[Dict[str, str]] = None,
    contact_speakers: Optional[Dict[str, str]] = None,
) -> Tuple[str, Dict[str, str], Dict[str, int]]:
    """
    Core renderer: turns raw transcript blocks into a Markdown body and returns:
      (body_md, final_map, id_block_counts)

    - No YAML is produced here; callers add headers via write_transcript_file().
    - Name attribution prioritizes explicit labels, then first-appearance assignment
      against a preferred name order inferred from titles/participants.
    """
    stored_overrides = stored_overrides or {}
    desired_participants = desired_participants or []
    db_participants_list = db_participants_list or []

    # 1) Build preference order of names (me first if present)
    pref_names = _merge_name_sources(
        quill_title=quill_title,
        meeting_title=meeting_title,
        db_participants=", ".join(db_participants_list),
        desired_participants=desired_participants,
    )
    if ME_FULLNAME not in pref_names:
        pref_names.insert(0, ME_FULLNAME)

    # 1.5) NEW: Enhance speaker attribution with context-aware consolidation
    # 1.5) NEW: Enhance speaker attribution with context-aware consolidation
    # DISABLED: Consolidation can cause issues in larger meetings
    # if ENABLE_SPEAKER_CONSOLIDATION:
    #     blocks = _enhance_speaker_attribution_with_context(
    #         blocks, meeting_title, quill_title, pref_names
    #     )

    # 2) Group contiguous blocks by stable speaker key (after consolidation)
    grouped_pairs = _group_consecutive_blocks(blocks)

    # 3) Build stable mapping from speaker_key -> final display name (after consolidation)
    mapping = _first_appearance_mapping(
        blocks,
        pref_names=pref_names,
        overrides=stored_overrides,
        quill_title=quill_title,
        meeting_title=meeting_title,
    )
    
    # 3.5) Override with ContactMeeting speaker mappings (most accurate)
    if contact_speakers:
        for speaker_key, name in contact_speakers.items():
            if name and not _looks_like_title_fragment(name, quill_title, meeting_title):
                mapping[speaker_key] = name
        
        # Auto-add local speaker if missing from ContactMeeting
        me_speaker_id = _pick_me_speaker_id(blocks)
        if me_speaker_id:
            me_speaker_key = f"id:{me_speaker_id}"
            if me_speaker_key not in contact_speakers:
                # Add local speaker to mapping if not already present
                if me_speaker_key not in mapping or mapping[me_speaker_key] == "Speaker":
                    mapping[me_speaker_key] = ME_FULLNAME

    # 4) Produce Markdown body lines and id->block count diagnostics
    # NOTE: Speaker attribution removed for cleaner LLM parsing
    id_counts: Dict[str, int] = {}
    body_lines: List[str] = []
    for key, utterance in grouped_pairs:
        id_counts[key] = id_counts.get(key, 0) + 1
        utt = re.sub(r"\s+\n\s+|\n+", " ", (utterance or "").strip())
        if not utt:
            continue
        body_lines.append(f"{utt}\n")

    body_md = "\n".join(body_lines).strip()
    return body_md, mapping, id_counts


def render_transcript_markdown(
    row_or_blocks,
    *,
    meeting_date: Optional[str] = None,   # kept for backward compat (unused by body)
    meeting_title: Optional[str] = None,
    quill_meeting_id: Optional[str] = None,
    quill_title: Optional[str] = None,
    quill_start_ms: Optional[int] = None,
    quill_end_ms: Optional[int] = None,
    summary_note_filename: Optional[str] = None,   # not used here; YAML added by writer
    db_participants_str: Optional[str] = None,
    desired_participants: Optional[List[str]] = None,
    stored_overrides: Optional[Dict[str, str]] = None,
) -> Tuple[str, Dict[str, str], Dict[str, int]]:
    """
    Compatibility shim used across routes:

    - If called with a MeetingRow (as existing call sites do), it extracts
      blocks and metadata, loads any saved label overrides, and returns:
        (body_md, final_map, id_counts)

    - If called with a blocks list + kwargs, it renders directly.

    NOTE: This function no longer produces YAML frontmatter; callers should
    continue using write_transcript_file(...) to add headers.
    """
    # Case A: legacy call style → render_transcript_markdown(row)
    if isinstance(row_or_blocks, MeetingRow):
        m: MeetingRow = row_or_blocks
        # Parse raw blocks out of the MeetingRow
        blocks, meta = _parse_audio_blocks(m.audio_transcript or "")
        if not blocks:
            # Nothing to render
            return "", {}, {}

        # Derive participant names from speakers/participants columns
        db_parts = derive_db_participants(m)

        # Load any saved manual label overrides
        overrides = load_label_override(m.id)

        # Get speaker mappings from ContactMeeting table (most accurate)
        speakers_map = {}
        try:
            with db_connect() as con:
                cur = con.cursor()
                contact_speakers = fetch_contact_meeting_speakers(cur, m.id)
                for sid, name in contact_speakers.items():
                    speakers_map[f"id:{sid}"] = name
        except Exception as e:
            app.logger.warning(f"Failed to get ContactMeeting speakers for {m.id}: {e}")
            # Fallback to speakers_json
            speakers_map = _build_speaker_map_from_speakers_json(m.speakers_json)

        return _render_transcript_body(
            blocks,
            meeting_title=meeting_title or m.title,
            quill_meeting_id=m.id,
            quill_title=m.title,
            db_participants_list=db_parts,
            desired_participants=desired_participants or [],
            stored_overrides=overrides,
            contact_speakers=speakers_map,
        )

    # Case B: new call style → render_transcript_markdown(blocks, **kwargs)
    blocks = row_or_blocks or []
    overrides = stored_overrides or {}
    db_list: List[str] = []
    if db_participants_str:
        db_list = split_participants_string(db_participants_str)

    return _render_transcript_body(
        blocks,
        meeting_title=meeting_title,
        quill_meeting_id=quill_meeting_id,
        quill_title=quill_title,
        db_participants_list=db_list,
        desired_participants=desired_participants or [],
        stored_overrides=overrides,
    )

# --- END PATCH ---

# ---------- Title/participant helpers ----------
TITLE_CLEAN_RE = re.compile(r"[^a-z0-9\s]+")
NAME_TOKEN_RE = re.compile(r"[A-Za-z']+")

def normalize_title(s: str) -> str:
    s = (s or "").strip().lower()
    s = TITLE_CLEAN_RE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def title_similarity(a: str, b: str) -> float:
    na, nb = normalize_title(a), normalize_title(b)
    if not na or not nb:
        return 0.0
    
    # Basic sequence similarity
    basic_similarity = difflib.SequenceMatcher(None, na, nb).ratio()
    
    # Enhanced matching for common patterns
    enhanced_score = enhanced_title_matching(na, nb)
    
    # Return the higher of the two scores
    return max(basic_similarity, enhanced_score)

def enhanced_title_matching(title_a: str, title_b: str) -> float:
    """Enhanced title matching that handles common meeting title variations."""
    
    # Extract key components from both titles
    components_a = extract_title_components(title_a)
    components_b = extract_title_components(title_b)
    
    if not components_a or not components_b:
        return 0.0
    
    # Calculate component-based similarity
    component_score = calculate_component_similarity(components_a, components_b)
    
    # Boost score for 1:1 meetings with matching participants
    if components_a.get('meeting_type') == '1:1' and components_b.get('meeting_type') == '1:1':
        if components_a.get('participants') and components_b.get('participants'):
            participant_overlap = len(set(components_a['participants']) & set(components_b['participants']))
            if participant_overlap > 0:
                component_score = min(1.0, component_score + 0.3)
    
    return component_score

def extract_title_components(title: str) -> dict:
    """Extract key components from a meeting title."""
    components = {
        'meeting_type': None,
        'participants': [],
        'topic': None
    }
    
    title_lower = title.lower()
    
    # Extract meeting type - check both original and normalized versions
    if ('1:1' in title_lower or '1 on 1' in title_lower or 
        '1 1' in title_lower or title_lower.startswith('1 1')):
        components['meeting_type'] = '1:1'
    elif 'sync' in title_lower:
        components['meeting_type'] = 'sync'
    elif 'standup' in title_lower:
        components['meeting_type'] = 'standup'
    elif 'retro' in title_lower:
        components['meeting_type'] = 'retro'
    
    # Extract common participant names (case-insensitive)
    # This is a basic heuristic - users may want to customize this list
    common_names = [
        'alex', 'alx', 'john', 'jane', 'mike', 'sarah', 'emily', 'david',
        'james', 'mary', 'robert', 'lisa', 'william', 'jennifer', 'michael'
    ]
    
    for name in common_names:
        if name in title_lower:
            components['participants'].append(name)
    
    # Extract topic (everything after the colon, if present)
    if ':' in title:
        topic_part = title.split(':', 1)[1].strip()
        if topic_part:
            components['topic'] = topic_part.lower()
    
    return components

def calculate_component_similarity(comp_a: dict, comp_b: dict) -> float:
    """Calculate similarity based on extracted components."""
    score = 0.0
    total_weight = 0.0
    
    # Meeting type similarity (weight: 0.4)
    if comp_a.get('meeting_type') and comp_b.get('meeting_type'):
        total_weight += 0.4
        if comp_a['meeting_type'] == comp_b['meeting_type']:
            score += 0.4
    
    # Participant similarity (weight: 0.4)
    if comp_a.get('participants') and comp_b.get('participants'):
        total_weight += 0.4
        participants_a = set(comp_a['participants'])
        participants_b = set(comp_b['participants'])
        if participants_a and participants_b:
            overlap = len(participants_a & participants_b)
            union = len(participants_a | participants_b)
            participant_similarity = overlap / union if union > 0 else 0.0
            score += 0.4 * participant_similarity
    
    # Topic similarity (weight: 0.2)
    if comp_a.get('topic') and comp_b.get('topic'):
        total_weight += 0.2
        topic_similarity = difflib.SequenceMatcher(None, comp_a['topic'], comp_b['topic']).ratio()
        score += 0.2 * topic_similarity
    
    # Return normalized score
    return score / total_weight if total_weight > 0 else 0.0

def normalize_person_token(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"\([^)]*\)", "", s)
    s = re.sub(r"[^a-z'\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def tokenize_name(s: str) -> List[str]:
    return [t for t in NAME_TOKEN_RE.findall(s.lower()) if t]

def expand_aliases(names: List[str]) -> List[str]:
    out = []
    alias_set = {a.lower() for a in MY_ALIASES}
    for n in names:
        nl = (n or "").strip().lower()
        if not nl:
            continue
        if nl in alias_set:
            out.append(MY_CANONICAL_NAME)
        else:
            out.append(n)
    return out

def split_participants_string(db_value: Optional[str]) -> List[str]:
    if not db_value:
        return []
    parts = re.split(r"[,&]| and ", db_value, flags=re.IGNORECASE)
    parts = [normalize_person_token(p) for p in parts if p and p.strip()]
    parts = expand_aliases(parts)
    parts = [p.title() for p in parts if p]
    return [p for p in parts if p]

def fuzzy_name_match(want: str, have: str) -> bool:
    wt = set(tokenize_name(normalize_person_token(want)))
    ht = set(tokenize_name(normalize_person_token(have)))
    if not wt or not ht:
        return False
    alias_set = {a.lower() for a in MY_ALIASES}
    if any(t in alias_set for t in wt):
        wt |= set(tokenize_name(MY_CANONICAL_NAME))
    return len(wt & ht) >= 1

def participant_overlap_fuzzy(desired: List[str], db_parts: List[str]) -> float:
    if not desired:
        return 0.0
    desired_norm = expand_aliases(desired)
    hits = 0
    for w in desired_norm:
        if any(fuzzy_name_match(w, h) for h in db_parts):
            hits += 1
    return hits / max(len(desired_norm), 1)

# ---------- Local day window ----------
def local_day_bounds_ms(date_str: str) -> Tuple[int, int]:
    dt_noon_utc = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(hours=12)
    half = WINDOW_HOURS * 60 * 60 * 1000
    center = int(dt_noon_utc.timestamp() * 1000)
    return center - half, center + half

def same_local_calendar_day(meeting_date: str, start_ms: Optional[int]) -> bool:
    if start_ms is None:
        return False
    dt_utc = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)
    month = dt_utc.month
    offset_hours = -5 if 3 <= month <= 11 else -6
    dt_local = dt_utc + timedelta(hours=offset_hours)
    return dt_local.strftime("%Y-%m-%d") == meeting_date

# ---------- DB ----------
@dataclass
class MeetingRow:
    id: str
    title: str
    participants: Optional[str]
    speakers_json: Optional[str]
    start: Optional[int]
    end: Optional[int]
    audio_transcript: Optional[str]

    def brief(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "participants": self.participants,
            "start_ms": self.start,
            "end_ms": self.end,
            "start_iso": ms_to_iso(self.start),
            "end_iso": ms_to_iso(self.end),
            "length_min": ms_length_min(self.start, self.end),
            "has_audio": bool(self.audio_transcript),
        }

_DB_COL_CACHE: Optional[set] = None

def db_connect() -> sqlite3.Connection:
    con = sqlite3.connect(str(QUILL_DB_PATH))
    con.row_factory = sqlite3.Row
    return con

def meeting_columns(con: sqlite3.Connection) -> set:
    global _DB_COL_CACHE
    if _DB_COL_CACHE is not None:
        return _DB_COL_CACHE
    cols = set()
    try:
        cur = con.execute("PRAGMA table_info(Meeting);")
        for row in cur.fetchall():
            cols.add(row[1])
    except Exception:
        pass
    _DB_COL_CACHE = cols
    return cols

def select_clause_for_meeting(con: sqlite3.Connection) -> str:
    cols = meeting_columns(con)
    parts = ["id", "title", "participants", "start", "end", "audio_transcript"]
    if "speakers" in cols:
        parts.insert(3, "speakers")
    return ", ".join(parts)

def fetch_contact_meeting_speakers(cur: sqlite3.Cursor, meeting_id: str) -> Dict[str, str]:
    """
    Fetch speaker mappings from ContactMeeting table for better speaker identification.
    Returns: {speaker_id: suggested_name}
    """
    # Use the database module for better error handling
    return get_contact_meeting_speakers(meeting_id)

def row_to_meeting(row: sqlite3.Row | None) -> Optional[MeetingRow]:
    if not row:
        return None
    speakers_json = row["speakers"] if "speakers" in row.keys() else None
    return MeetingRow(
        id=row["id"],
        title=row["title"],
        participants=row["participants"],
        speakers_json=speakers_json,
        start=row["start"],
        end=row["end"],
        audio_transcript=row["audio_transcript"],
    )

def fetch_by_id(cur: sqlite3.Cursor, con: sqlite3.Connection, meeting_id: str) -> Optional[MeetingRow]:
    """Get meeting by ID using database module."""
    row = get_meeting_by_id(meeting_id)
    return row_to_meeting(row)

def fetch_overlap_window(cur: sqlite3.Cursor, con: sqlite3.Connection, lo_ms: int, hi_ms: int, limit: int = 600) -> List[MeetingRow]:
    """Get meetings in time window using database module."""
    rows = get_meetings_in_window(lo_ms, hi_ms, limit)
    return [row_to_meeting(r) for r in rows if r]

def fetch_start_in_window(cur: sqlite3.Cursor, con: sqlite3.Connection, lo_ms: int, hi_ms: int, like: Optional[str] = None, limit: int = 600) -> List[MeetingRow]:
    if like:
        sql = f"""SELECT {select_clause_for_meeting(con)}
                  FROM Meeting
                  WHERE deleteDate IS NULL AND start BETWEEN ? AND ? AND title LIKE ?
                  ORDER BY start ASC LIMIT ?"""
        cur.execute(sql, (lo_ms, hi_ms, f"%{like}%", limit))
    else:
        sql = f"""SELECT {select_clause_for_meeting(con)}
                  FROM Meeting
                  WHERE deleteDate IS NULL AND start BETWEEN ? AND ?
                  ORDER BY start ASC LIMIT ?"""
        cur.execute(sql, (lo_ms, hi_ms, limit))
    return [row_to_meeting(r) for r in cur.fetchall() if r]

def derive_db_participants(m: MeetingRow, cur: sqlite3.Cursor = None) -> List[str]:
    """
    Get participants from ContactMeeting table first, fallback to speakers_json, then participants string.
    """
    # Try ContactMeeting table first (most accurate)
    if cur:
        try:
            contact_speakers = fetch_contact_meeting_speakers(cur, m.id)
            if contact_speakers:
                names = list(contact_speakers.values())
                parts = [normalize_person_token(n) for n in names if n]
                parts = expand_aliases(parts)
                parts = [p.title() for p in parts if p]
                parts = [p for p in parts if p]
                if parts:
                    return parts
        except Exception as e:
            app.logger.warning(f"Failed to get participants from ContactMeeting for {m.id}: {e}")
    
    # Fallback to speakers_json
    if m.speakers_json:
        try:
            data = json.loads(m.speakers_json)
            names = []
            if isinstance(data, list):
                for obj in data:
                    if isinstance(obj, dict):
                        nm = obj.get("name") or obj.get("display_name")
                        if nm and isinstance(nm, str):
                            names.append(nm)
            elif isinstance(data, dict) and "speakers" in data and isinstance(data["speakers"], list):
                for obj in data["speakers"]:
                    if isinstance(obj, dict):
                        nm = obj.get("name") or obj.get("display_name")
                        if nm and isinstance(nm, str):
                            names.append(nm)
            if names:
                parts = [normalize_person_token(n) for n in names if n]
                parts = expand_aliases(parts)
                parts = [p.title() for p in parts if p]
                parts = [p for p in parts if p]
                if parts:
                    return parts
        except Exception:
            pass
    
    # Final fallback to participants string
    return split_participants_string(m.participants)

# ---------- Scoring ----------
WEIGHT_TABLE = {
    # participants dominate for 1:1
    "1-on-1":        {"overlap": 0.70, "title": 0.15, "time": 0.15},
    # internal still values title, but overlap increased
    "internal-sync": {"overlap": 0.65, "title": 0.20, "time": 0.15},
    # external meetings (Stripe etc.) overlap is king
    "external-sync": {"overlap": 0.75, "title": 0.10, "time": 0.15},
    "note-to-self":  {"overlap": 0.60, "title": 0.10, "time": 0.30},
    "default":       {"overlap": 0.60, "title": 0.25, "time": 0.15},
}

def compute_score(
    session_type: str,
    meeting_date: str,
    needle_title: str,
    desired_participants: List[str],
    center_ms: int, lo_ms: int, hi_ms: int,
    m: MeetingRow,
    cur: sqlite3.Cursor = None,
    transcript_snippet: Optional[str] = None
) -> Tuple[float, Dict[str, float]]:
    # Normalize participants - use ContactMeeting table if cursor available
    db_parts = derive_db_participants(m, cur)
    desired = [p.strip() for p in (desired_participants or []) if p and p.strip()]
    # Base fuzzy overlap (0..1) you already use
    overlap = participant_overlap_fuzzy(desired, db_parts)

    # Title similarity (0..1)
    title_score = title_similarity(needle_title or "", m.title or "")

    # Time proximity (0..1) centered on the day (or Quill start/end center)
    t_mid = (m.start if m.start is not None else center_ms + m.end if m.end is not None else center_ms)
    if m.start is not None and m.end is not None:
        t_mid = (m.start + m.end) // 2
    span = float(max(1, hi_ms - lo_ms))
    time_score = max(0.0, 1.0 - (abs((t_mid or center_ms) - center_ms) / span))

    same_day_bonus = 0.10 if same_local_calendar_day(meeting_date, m.start) else 0.0

    # --- NEW: Transcript snippet matching ---
    transcript_score = 0.0
    if transcript_snippet and m.audio_transcript:
        try:
            # Parse the audio transcript to get the beginning text
            blocks, _ = _parse_audio_blocks(m.audio_transcript)
            if blocks:
                # Get the first few blocks to compare with the snippet
                beginning_text = " ".join([b.get("text", "") for b in blocks[:10]])
                beginning_text = beginning_text.strip()
                
                if beginning_text:
                    # Clean up both texts for comparison
                    snippet_clean = re.sub(r'\s+', ' ', transcript_snippet.strip())
                    beginning_clean = re.sub(r'\s+', ' ', beginning_text[:len(snippet_clean) * 2])  # Compare first 2x snippet length
                    
                    # Normalize Unicode quotes to regular quotes for better matching
                    snippet_clean = snippet_clean.replace('″', '"').replace('"', '"').replace('"', '"')
                    snippet_clean = snippet_clean.replace("'", "'").replace("'", "'")
                    
                    # Filter out speaker labels that might interfere with matching
                    # Remove patterns like "Speaker 1:", "Speaker 2:", or any name followed by colon
                    snippet_clean = re.sub(r'Speaker \d+:\s*', '', snippet_clean)
                    beginning_clean = re.sub(r'Speaker \d+:\s*', '', beginning_clean)
                    # Also remove actual names followed by colons (from webhook speaker attribution)
                    snippet_clean = re.sub(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*:\s*', '', snippet_clean)
                    beginning_clean = re.sub(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*:\s*', '', beginning_clean)
                    
                    # Use sequence matcher for similarity
                    similarity = difflib.SequenceMatcher(None, snippet_clean.lower(), beginning_clean.lower()).ratio()
                    transcript_score = similarity
                    
                    # High confidence if we get a very good match
                    if similarity > 0.8:
                        transcript_score = min(1.0, similarity + 0.2)  # Boost high matches
                    
                    # Additional boost for exact phrase matches (even with speaker labels removed)
                    if snippet_clean.lower() in beginning_clean.lower():
                        transcript_score = min(1.0, transcript_score + 0.1)
                    
                    # If beginning match is poor, try searching further into the transcript
                    # This handles cases where the snippet might be from later in the conversation
                    if transcript_score < 0.3 and len(blocks) > 10:
                        # Search in the first 50 blocks for better matches
                        extended_text = " ".join([b.get("text", "") for b in blocks[:50]])
                        extended_clean = re.sub(r'\s+', ' ', extended_text)
                        extended_clean = re.sub(r'Speaker \d+:\s*', '', extended_clean)
                        extended_clean = re.sub(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*:\s*', '', extended_clean)
                        
                        # Check if snippet appears anywhere in the extended text
                        if snippet_clean.lower() in extended_clean.lower():
                            # Found it further in the transcript - give a moderate score
                            transcript_score = max(transcript_score, 0.4)
                        
                        # Also try sequence matching on extended text
                        extended_similarity = difflib.SequenceMatcher(None, snippet_clean.lower(), extended_clean[:len(snippet_clean) * 5]).ratio()
                        transcript_score = max(transcript_score, extended_similarity * 0.8)
        except Exception as e:
            # Log but don't fail the matching
            app.logger.debug(f"Failed to parse transcript for snippet matching: {e}")

    # --- NEW: set-logic adjustments ---
    # Treat desired participants as a (mostly) exact roster for external syncs.
    set_bonus = 0.0
    set_penalty = 0.0

    dset = {p.lower() for p in desired}
    bset = {p.lower() for p in db_parts}

    if desired:
        # Exact set match (order-insensitive) gets a big boost.
        if dset == bset:
            set_bonus += 0.25
        else:
            # If db contains *all* desired plus extras → small penalty (superset)
            if dset.issubset(bset):
                # Penalty scales with how many extras there are, but capped
                extras = max(0, len(bset) - len(dset))
                set_penalty += min(0.12, 0.04 * extras)
            # If db is missing any desired → heavy penalty
            missing = len(dset - bset)
            if missing > 0:
                set_penalty += min(0.35, 0.18 + 0.10 * (missing - 1))

        # Special case: "1-on-1" — prefer exactly two people (you + 1)
        if session_type == "1-on-1":
            if len(bset) == 2 and len(dset) == 2 and dset == bset:
                set_bonus += 0.12
            elif len(bset) > 2:
                set_penalty += 0.10

    # Group-size sanity for note-to-self
    group_size_penalty = 0.0
    if session_type == "note-to-self" and len(db_parts) > 1:
        group_size_penalty = 0.15

    # Compose score - include transcript score in weighting
    w = WEIGHT_TABLE.get(session_type, WEIGHT_TABLE["default"])
    
    # Adjust weights to include transcript matching
    # If we have a transcript snippet, make it the dominant factor
    if transcript_snippet:
        # Give transcript matching much higher weight since it's the most reliable
        transcript_weight = 0.60  # 60% weight for transcript matching
        adjusted_overlap_weight = w["overlap"] * 0.2  # Reduce overlap weight significantly
        adjusted_title_weight = w["title"] * 0.2      # Reduce title weight significantly (unreliable)
        adjusted_time_weight = w["time"] * 0.5        # Keep time weight moderate
        
        composite = (
            (adjusted_overlap_weight * overlap) +
            (adjusted_title_weight * title_score) +
            (adjusted_time_weight * time_score) +
            (transcript_weight * transcript_score) +
            same_day_bonus +
            set_bonus -
            set_penalty -
            group_size_penalty
        )
    else:
        # Original scoring without transcript matching
        composite = (
            (w["overlap"] * overlap) +
            (w["title"] * title_score) +
            (w["time"]  * time_score) +
            same_day_bonus +
            set_bonus -
            set_penalty -
            group_size_penalty
        )

    parts = {
        "overlap": round(overlap, 3),
        "title_score": round(title_score, 3),
        "time_score": round(time_score, 3),
        "transcript_score": round(transcript_score, 3),
        "same_day_bonus": round(same_day_bonus, 3),
        "set_bonus": round(set_bonus, 3),
        "set_penalty": round(set_penalty, 3),
        "group_size_penalty": round(group_size_penalty, 3),
        "composite": round(composite, 3),
        "db_parts": db_parts,
        "desired_parts": desired,
    }
    return composite, parts

# ---------- Pending ----------
def pending_path_for(summary_path: Path) -> Path:
    return summary_path.with_suffix(summary_path.suffix + PENDING_SUFFIX)

def save_pending(summary_path: Path, payload: dict, fm: dict) -> None:
    title = payload.get("meeting_title")
    date = payload.get("meeting_date")
    data = {
        "meeting_title": title,
        "meeting_date": date,
        "quill_meeting_id": payload.get("quill_meeting_id"),
        "quill_start_ms": payload.get("quill_start_ms"),
        "quill_end_ms": payload.get("quill_end_ms"),
        "quill_title": payload.get("quill_title"),
        "transcript_snippet": payload.get("transcript_snippet"),
        "summary_path": str(summary_path),
        "session_type": (fm.get("session_type") or "").strip() or infer_session_type_from_title(title),
        "participants": fm.get("participants") or [],
    }
    pending_path_for(summary_path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def infer_session_type_from_title(meeting_title: Optional[str]) -> str:
    t = (meeting_title or "").lower()
    if "1:1" in t or "1-1" in t or "1 on 1" in t:
        return "1-on-1"
    if "sync" in t and "external" in t:
        return "external-sync"
    if "sync" in t:
        return "internal-sync"
    if "note" in t and "self" in t:
        return "note-to-self"
    return "default"

def load_pending(pending_file: Path) -> dict:
    return json.loads(pending_file.read_text(encoding="utf-8"))

def list_all_pendings() -> List[Path]:
    return list(SUMMARIES_ROOT.rglob(f"*{PENDING_SUFFIX}"))

# ---------- Matching ----------
def find_best_candidate(pd: dict) -> Tuple[Optional['MeetingRow'], Optional[str], List[dict]]:
    debug_rows: List[dict] = []
    meeting_date = pd["meeting_date"]
    meeting_title = pd["meeting_title"]
    session_type = pd.get("session_type") or "default"
    desired_participants = pd.get("participants") or []

    if not QUILL_DB_PATH.exists():
        app.logger.info("[i] quill.db not found at %s", QUILL_DB_PATH)
        return None, None, debug_rows

    with db_connect() as con:
        cur = con.cursor()

        qid = pd.get("quill_meeting_id")
        if qid:
            row = fetch_by_id(cur, con, qid)
            if row and row.audio_transcript:
                return row, "id", debug_rows

        lo_ms, hi_ms = local_day_bounds_ms(meeting_date)
        center = (lo_ms + hi_ms) // 2
        if pd.get("quill_start_ms") is not None or pd.get("quill_end_ms") is not None:
            qs, qe = pd.get("quill_start_ms"), pd.get("quill_end_ms")
            center = int(((qs or qe) + (qe or qs)) // 2) if (qs and qe) else int(qs or qe)

        cands = fetch_overlap_window(cur, con, lo_ms, hi_ms, limit=800)

        look_title = pd.get("quill_title") or meeting_title

        scored: List[Tuple[float, MeetingRow, Dict[str, float]]] = []
        transcript_snippet = pd.get("transcript_snippet")
        
        for m in cands:
            if not m or not m.audio_transcript:
                continue
            # inside: for m in cands:
            cand_parts = derive_db_participants(m, cur)
            
            # Skip participant filtering entirely when we have a transcript snippet
            # Quill's participant list is unreliable for many reasons:
            # - Email-based names (jn@stripe.com -> Jn)
            # - No-shows from meeting invites
            # - Undefined speakers (Speaker 1, Speaker 2)
            # - Uninvited participants who join calls
            # - Impromptu meetings not on Google Calendar
            # - Conference room attendees
            # - Manual speaker naming in Quill UI
            # Transcript matching should be the primary and most reliable mechanism
            if desired_participants and not transcript_snippet:
                # Only apply participant filtering as a last resort when no transcript snippet is available
                # And even then, be more lenient about missing participants
                dset = {p.lower() for p in desired_participants}
                bset = {p.lower() for p in cand_parts}
                
                # Check if we have any meaningful overlap (at least 1 person in common)
                overlap_count = len(dset.intersection(bset))
                if overlap_count == 0 and len(dset) >= 2:
                    # Only skip if we have no overlap at all AND we have multiple desired participants
                    # This allows for cases where some participants didn't show or were renamed
                    continue

            score, parts = compute_score(
                session_type=session_type,
                meeting_date=meeting_date,
                needle_title=look_title,
                desired_participants=desired_participants,
                center_ms=center, lo_ms=lo_ms, hi_ms=hi_ms, m=m, cur=cur,
                transcript_snippet=transcript_snippet
            )
            meta = {**m.brief(), **parts, "session_type": session_type}
            scored.append((score, m, meta))

        scored.sort(key=lambda t: t[0], reverse=True)
        debug_rows.extend([meta for _, __, meta in scored[:25]])

        thresh = CONF_THRESHOLDS.get(session_type, CONF_THRESHOLDS["default"])

        if scored:
            top_score, top_row, top_meta = scored[0]
            # Minimum participant overlap requirement when frontmatter supplies participants.
            desired = top_meta.get("desired_parts") or []
            db_parts = top_meta.get("db_parts") or []

            min_overlap = 0.60 if session_type in ("external-sync", "internal-sync") else 0.50
            ok_overlap = (top_meta.get("overlap", 0.0) >= min_overlap)

            exact_set = False
            if desired:
                dset = {p.lower() for p in desired}
                bset = {p.lower() for p in db_parts}
                exact_set = (dset == bset)

            # Check for transcript similarity first (most reliable mechanism)
            transcript_s = float(top_meta.get("transcript_score", 0.0))
            if transcript_s >= 0.15:  # Very low threshold since transcript matching is now primary
                return top_row, "transcript", debug_rows
            
            # Accept if (score >= thresh) AND (overlap is solid OR exact set match)
            # OR if we have perfect participant overlap with reasonable transcript score
            overlap = float(top_meta.get("overlap", 0.0))
            if (top_score >= thresh and (ok_overlap or exact_set)) or (overlap >= 0.9 and transcript_s >= 0.1):
                return top_row, "ranked_window", debug_rows

            # If the best scored candidate is close AND participant overlap is strong, accept.
        if scored:
            top_m = scored[0][1]
            top_meta = scored[0][2]
            overlap = float(top_meta.get("overlap", 0.0))
            title_s = float(top_meta.get("title_score", 0.0))
            transcript_s = float(top_meta.get("transcript_score", 0.0))
            comp = float(top_meta.get("composite", 0.0))
            # Fallback: Strong participants OR good transcript match OR moderate title match
            # Note: Title similarity is unreliable since JSON payload titles rarely match Quill titles
            if (overlap >= 0.50 and title_s >= 0.20 and comp >= (thresh - 0.05)) or transcript_s >= 0.3 or title_s >= 0.25:
                return top_m, "ranked_overlap", debug_rows

        items = fetch_start_in_window(cur, con, lo_ms, hi_ms, like=None, limit=500)
        best = None
        best_s = 0.0
        for m in items:
            if not m or not m.audio_transcript:
                continue
            s = title_similarity(look_title or "", m.title or "")
            if s > best_s:
                best_s, best = s, m
        if best and best_s >= 0.70:
            return best, "title", debug_rows

        return None, None, debug_rows

# ---------- Cross-link ----------
def inject_backlink_into_summary(summary_path: Path, transcript_filename: str) -> None:
    try:
        text = summary_path.read_text(encoding="utf-8")
    except Exception:
        return
    if not text.startswith("---"):
        return
    end = text.find("\n---", 3)
    if end == -1:
        return
    head = text[:end + 4]
    body = text[end + 4:]

    wikilink = f"[[{transcript_filename}]]"
    if wikilink in head:
        return

    if "\nlinks:" in head:
        new_head = head.replace("\nlinks:", f"\nlinks:\n  - '{wikilink}'", 1)
    else:
        new_head = head[:-4] + f"\nlinks:\n  - '{wikilink}'\n---\n"

    try:
        summary_path.write_text(new_head + body, encoding="utf-8")
    except Exception:
        pass

def write_transcript_file(transcript_path: Path, meeting_date: str, meeting_title: str, summary_filename: str, row: 'MeetingRow', body_md: str, session_type: str) -> None:
    yaml_lines = [
        "---",
        f"date: {meeting_date}",
        f'title: "{meeting_date} {meeting_title} – Transcript"',
        f"project: team-meeting",
        f"session_type: {session_type or 'default'}",
        "source: quill",
        "tags:",
        "  - transcript",
        "  - quill",
        "links:",
        f"  - '[[{summary_filename}]]'",
        f"quill_meeting_id: {json.dumps(row.id)}",
        f"quill_title: {json.dumps(row.title)}",
        f"quill_start_ms: {row.start if row.start is not None else 'null'}",
        f"quill_end_ms: {row.end if row.end is not None else 'null'}",
        "---",
        "",
    ]
    content = "\n".join(yaml_lines) + body_md.strip() + "\n"
    transcript_path.write_text(content, encoding="utf-8")

# ---------- Routes ----------
@app.post("/relabel")
def relabel():
    """
    Body: {
      "meeting_id": "<Quill Meeting.id>",
      "mapping": {"<speaker_id>": "Correct Name", ...},
      "rewrite": true   # optional, if true and a transcript exists, rewrite it in-place
    }
    """
    data = request.get_json(force=True, silent=True) or {}
    meeting_id = data.get("meeting_id")
    mapping = data.get("mapping") or {}
    rewrite = bool(data.get("rewrite"))

    if not meeting_id or not isinstance(mapping, dict):
        return err_json("Need meeting_id and mapping {speaker_id: name}")

    save_label_override(meeting_id, mapping)

    # Optionally rewrite existing transcript for the same meeting_date/title if present
    rewritten = None
    if rewrite:
        if not QUILL_DB_PATH.exists():
            return err_json("Saved mapping, but quill.db not found for rewrite", status=404)

        with db_connect() as con:
            cur = con.cursor()
            row = fetch_by_id(cur, con, meeting_id)
            if not row or not row.audio_transcript:
                return err_json("Saved mapping, but meeting not found/has no audio", status=404)

        # Find any transcript that links to this meeting_id and rewrite it
        # (lightweight scan of the expected folder)
        ymd_candidates = []
        if row.start:
            dt = datetime.fromtimestamp(row.start/1000, tz=timezone.utc)
            ymd_candidates = [f"{dt.year:04d}-{dt.month:02d}"]
        else:
            # fallback: scan a couple of months
            now = datetime.now(timezone.utc)
            ymd_candidates = [f"{now.year:04d}-{now.month:02d}"]

        for yfolder in ymd_candidates:
            tdir = TRANSCRIPTS_ROOT / yfolder
            if not tdir.exists():
                continue
            for md in tdir.glob("* – Transcript.md"):
                try:
                    txt = md.read_text(encoding="utf-8")
                except Exception:
                    continue
                if f'quill_meeting_id: "{meeting_id}"' in txt:
                    # Re-render with new map
                    body_md, final_map, id_counts = render_transcript_markdown(row)
                    # Preserve YAML header; replace body
                    if "\n---\n" in txt:
                        head, _ = txt.split("\n---\n", 1)
                        head = head + "\n---\n"
                    else:
                        head = ""
                    md.write_text(head + body_md + ("\n" if not body_md.endswith("\n") else ""), encoding="utf-8")
                    rewritten = str(md)
                    break

    return ok_json({"ok": True, "saved_override": True, "rewritten": rewritten})

@app.get("/health")
def health():
    """Enhanced health check with database status."""
    db_health = check_database_health()
    return ok_json({
        "ok": True,
        "service": "quill-webhook",
        "notes_root": str(NOTES_ROOT),
        "db_exists": QUILL_DB_PATH.exists(),
        "database": db_health,
        "version": "2.0.0"
    })

@app.get("/config")
def config():
    return ok_json({
        "notes_root": str(NOTES_ROOT),
        "summaries_root": str(SUMMARIES_ROOT),
        "transcripts_root": str(TRANSCRIPTS_ROOT),
        "quill_db_path": str(QUILL_DB_PATH),
        "host": HOST,
        "port": PORT,
        "auto_reconcile_on_summary": AUTO_RECONCILE_ON_SUMMARY,
    })

# FLOW A — summary only
@app.post("/quill_summary")
def quill_summary():
    """Handle Quill summary webhook with enhanced validation and logging."""
    try:
        raw = request.data.decode("utf-8", errors="replace")
        logger.info("Webhook received", extra={"raw_bytes": len(raw)})
        payload = request.get_json(force=True, silent=True)
        if payload is None:
            payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        logger.error("Invalid JSON in webhook", extra={"error": str(e)})
        return err_json("Invalid JSON", status=400, extra={"detail": str(e)})

    if isinstance(payload, dict) and "data" in payload:
        nested = payload["data"]
        if isinstance(nested, str):
            try:
                payload = json.loads(nested)
            except json.JSONDecodeError as e:
                logger.error("Nested JSON parse failed", extra={"error": str(e)})
                return err_json("Nested JSON parse failed", status=400, extra={"detail": str(e)})
        elif isinstance(nested, dict):
            payload = nested

    # Validate webhook payload
    is_valid, error_msg = validate_webhook_payload(payload)
    if not is_valid:
        logger.error("Invalid webhook payload", extra={"error": error_msg, "payload_keys": list(payload.keys()) if isinstance(payload, dict) else "not_dict"})
        return err_json("Invalid payload", status=400, extra={"detail": error_msg})

    summary_md = payload.get("summary_markdown")
    meeting_title = payload.get("meeting_title")
    meeting_date = payload.get("meeting_date")
    
    # Log webhook reception
    log_webhook_received(meeting_title, meeting_date, len(raw))

    yfolder = yyyymm_from_date(meeting_date)
    summaries_dir = SUMMARIES_ROOT / yfolder
    ensure_dir(summaries_dir)

    base = safe_filename(f"{meeting_date} {meeting_title}")
    summary_filename = f"{base} – Summary.md"
    summary_path = summaries_dir / summary_filename

    try:
        summary_path.write_text(summary_md, encoding="utf-8")
        logger.info("Summary saved successfully", extra={"summary_path": str(summary_path)})
    except OSError as e:
        logger.error("Failed to write summary file", extra={"error": str(e), "summary_path": str(summary_path)})
        return err_json("Failed to write summary file", status=500, extra={"detail": str(e)})

    fm = parse_frontmatter(summary_md)
    save_pending(summary_path, payload, fm)
    pending_file = str(pending_path_for(summary_path))

    created = False
    matched_by = None
    matched_id = None
    top_candidates = []
    transcript_path_str = None

    if AUTO_RECONCILE_ON_SUMMARY:
        pd = load_pending(Path(pending_file))
        logger.info("Attempting auto-reconcile", extra={"pending_file": pending_file})

        row, reason, debug_rows = find_best_candidate(pd)
        meeting_date = pd["meeting_date"]
        meeting_title = pd["meeting_title"]
        session_type = pd.get("session_type") or "default"

        yfolder = yyyymm_from_date(meeting_date)
        transcripts_dir = TRANSCRIPTS_ROOT / yfolder
        ensure_dir(transcripts_dir)

        base = safe_filename(f"{meeting_date} {meeting_title}")
        transcript_filename = f"{base} – Transcript.md"
        transcript_path = transcripts_dir / transcript_filename

        thresh = CONF_THRESHOLDS.get(session_type, CONF_THRESHOLDS["default"])
        top_candidates = debug_rows[:10]

        if row and row.audio_transcript and reason in {"id", "ranked_window", "title", "transcript"}:
            top_score = top_candidates[0].get("composite", 0) if top_candidates else 0
            confident = (reason in {"id", "title", "transcript"}) or (top_score >= thresh)
            
            # Log matching result
            log_matching_result(meeting_title, session_type, top_score, thresh, confident, reason)
            
            if confident:
                body_md, final_map, id_counts = render_transcript_markdown(row)
                write_transcript_file(transcript_path, meeting_date, meeting_title, summary_path.name, row, body_md, session_type)
                inject_backlink_into_summary(summary_path, transcript_path.name)
                matched_id = row.id
                matched_by = reason
                created = True
                transcript_path_str = str(transcript_path)
                
                # Log successful processing
                log_meeting_processed(row.id, [], True)
                
                Path(pending_file).unlink(missing_ok=True)

    return ok_json({
        "ok": True,
        "saved_summary": str(summary_path),
        "pending_file": pending_file if not created else None,
        "auto_reconcile_attempted": AUTO_RECONCILE_ON_SUMMARY,
        "created_transcript": transcript_path_str,
        "matched_by": matched_by,
        "matched_id": matched_id,
        "candidates": top_candidates
    })

# FLOW B — auto reconcile all pendings
@app.post("/reconcile/auto")
def reconcile_auto():
    pendings = list_all_pendings()
    results = []
    for p in pendings:
        try:
            pd = load_pending(p)
            row, reason, debug_rows = find_best_candidate(pd)
            summary_path = Path(pd["summary_path"])
            meeting_date = pd["meeting_date"]
            meeting_title = pd["meeting_title"]
            session_type = pd.get("session_type") or "default"

            yfolder = yyyymm_from_date(meeting_date)
            transcripts_dir = TRANSCRIPTS_ROOT / yfolder
            ensure_dir(transcripts_dir)

            base = safe_filename(f"{meeting_date} {meeting_title}")
            transcript_filename = f"{base} – Transcript.md"
            transcript_path = transcripts_dir / transcript_filename

            created = False
            matched_id = None
            top_scores = debug_rows[:10]
            thresh = CONF_THRESHOLDS.get(session_type, CONF_THRESHOLDS["default"])

            if row and row.audio_transcript and reason in {"id", "ranked_window", "title", "transcript"}:
                confident = (reason in {"id", "title", "transcript"}) or (top_scores and top_scores[0].get("composite", 0) >= thresh)
                if confident:
                    body_md, final_map, id_counts = render_transcript_markdown(row)
                    write_transcript_file(transcript_path, meeting_date, meeting_title, summary_path.name, row, body_md, session_type)
                    inject_backlink_into_summary(summary_path, transcript_path.name)
                    matched_id = row.id
                    created = True
                    p.unlink(missing_ok=True)

            results.append({
                "pending": str(p),
                "summary": str(summary_path),
                "created_transcript": str(transcript_path) if created else None,
                "matched_by": reason if created else None,
                "matched_id": matched_id,
                "threshold": thresh,
                "candidates": top_scores
            })
        except Exception as e:
            results.append({"pending": str(p), "error": str(e)})

    return ok_json({"ok": True, "results": results})

# Manual pick
@app.post("/reconcile/pick")
def reconcile_pick():
    data = request.get_json(force=True, silent=True) or {}
    pending_path = Path(data.get("pending_path", ""))
    meeting_id = data.get("meeting_id")
    if not pending_path or not meeting_id:
        return err_json("Need pending_path and meeting_id")

    if not pending_path.exists():
        return err_json("pending_path not found", status=404)

    pd = load_pending(pending_path)
    meeting_date = pd.get("meeting_date")
    meeting_title = pd.get("meeting_title")
    session_type = pd.get("session_type") or "default"

    if not QUILL_DB_PATH.exists():
        return err_json("quill.db not found", status=404, extra={"path": str(QUILL_DB_PATH)})

    with db_connect() as con:
        cur = con.cursor()
        row = fetch_by_id(cur, con, meeting_id)
        if not row or not row.audio_transcript:
            return err_json("meeting not found or has no audio_transcript", status=404)

    summary_path = Path(pd["summary_path"])
    yfolder = yyyymm_from_date(meeting_date)
    transcripts_dir = TRANSCRIPTS_ROOT / yfolder
    ensure_dir(transcripts_dir)

    base = safe_filename(f"{meeting_date} {meeting_title}")
    transcript_filename = f"{base} – Transcript.md"
    transcript_path = transcripts_dir / transcript_filename

    body_md, final_map, id_counts = render_transcript_markdown(row)
    write_transcript_file(transcript_path, meeting_date, meeting_title, summary_path.name, row, body_md, session_type)
    inject_backlink_into_summary(summary_path, transcript_path.name)

    pending_path.unlink(missing_ok=True)

    return ok_json({
        "ok": True,
        "saved_transcript": str(transcript_path),
        "matched_id": row.id,
        "quill_title": row.title,
        "speaker_map": final_map,
        "id_block_counts": id_counts
    })

# Direct by known Meeting.id
@app.post("/quill_transcript")
def quill_transcript():
    data = request.get_json(force=True, silent=True) or {}
    meeting_id = data.get("meeting_id")
    meeting_date = data.get("meeting_date")
    meeting_title = data.get("meeting_title")

    if not meeting_id or not meeting_date or not meeting_title:
        return err_json("Need meeting_id, meeting_date, meeting_title")

    if not QUILL_DB_PATH.exists():
        return err_json("quill.db not found", status=404, extra={"path": str(QUILL_DB_PATH)})

    with db_connect() as con:
        cur = con.cursor()
        row = fetch_by_id(cur, con, meeting_id)
        if not row or not row.audio_transcript:
            return err_json("meeting not found or has no audio_transcript", status=404)

    yfolder = yyyymm_from_date(meeting_date)
    summaries_dir = SUMMARIES_ROOT / yfolder
    ensure_dir(summaries_dir)

    expected_base = safe_filename(f"{meeting_date} {meeting_title}")
    summary_filename = f"{expected_base} – Summary.md"
    summary_path = summaries_dir / summary_filename

    transcripts_dir = TRANSCRIPTS_ROOT / yfolder
    ensure_dir(transcripts_dir)
    transcript_filename = f"{expected_base} – Transcript.md"
    transcript_path = transcripts_dir / transcript_filename

    session_type = "default"
    if summary_path.exists():
        try:
            fm = parse_frontmatter(summary_path.read_text(encoding="utf-8"))
            session_type = fm.get("session_type") or "default"
        except Exception:
            pass

    body_md, final_map, id_counts = render_transcript_markdown(row)
    write_transcript_file(transcript_path, meeting_date, meeting_title, summary_path.name if summary_path.exists() else summary_filename, row, body_md, session_type)
    if summary_path.exists():
        inject_backlink_into_summary(summary_path, transcript_path.name)

    return ok_json({
        "ok": True,
        "saved_transcript": str(transcript_path),
        "linked_summary": str(summary_path) if summary_path.exists() else None,
        "quill_title": row.title,
        "speaker_map": final_map,
        "id_block_counts": id_counts
    })

# ---------- Debug: meetings ----------
@app.get("/debug/meetings")
def debug_meetings():
    date_str = request.args.get("date")
    if not date_str:
        return err_json("Missing ?date=YYYY-MM-DD")
    hours = int(request.args.get("hours", "48"))
    like = request.args.get("like")
    person = request.args.get("person")

    lo_ms, hi_ms = local_day_bounds_ms(date_str)
    half = hours * 60 * 60 * 1000
    center = (lo_ms + hi_ms) // 2
    lo_ms = center - half
    hi_ms = center + half

    if not QUILL_DB_PATH.exists():
        return err_json("quill.db not found", status=404, extra={"path": str(QUILL_DB_PATH)})

    with db_connect() as con:
        cur = con.cursor()
        items = fetch_start_in_window(cur, con, lo_ms, hi_ms, like=like, limit=800)
        out = []
        for m in items:
            d = m.brief()
            if person:
                parts = derive_db_participants(m)
                if not any(fuzzy_name_match(person, p) for p in parts):
                    continue
            out.append(d)
        return ok_json({
            "ok": True,
            "window": {"lo_iso": ms_to_iso(lo_ms), "hi_iso": ms_to_iso(hi_ms), "hours": hours},
            "count": len(out),
            "items": out
        })

# ---------- Debug: diarization map ----------
@app.get("/debug/diarization_map")
def debug_diarization_map():
    meeting_id = request.args.get("meeting_id")
    if not meeting_id:
        return err_json("Missing ?meeting_id=<id>")
    if not QUILL_DB_PATH.exists():
        return err_json("quill.db not found", status=404, extra={"path": str(QUILL_DB_PATH)})
    with db_connect() as con:
        cur = con.cursor()
        m = fetch_by_id(cur, con, meeting_id)
        if not m or not m.audio_transcript:
            return err_json("meeting not found or has no audio_transcript", status=404)
    body_md, final_map, id_counts = render_transcript_markdown(m)
    preview = body_md[:600] + ("…" if len(body_md) > 600 else "")
    return ok_json({
        "ok": True,
        "meeting": m.brief(),
        "final_map": final_map,
        "id_block_counts": id_counts,
        "preview": preview
    })

# ---------- Debug: speaker consolidation ----------
@app.get("/debug/speaker_consolidation")
def debug_speaker_consolidation():
    meeting_id = request.args.get("meeting_id")
    if not meeting_id:
        return err_json("Missing ?meeting_id=<id>")
    if not QUILL_DB_PATH.exists():
        return err_json("quill.db not found", status=404, extra={"path": str(QUILL_DB_PATH)})
    
    with db_connect() as con:
        cur = con.cursor()
        m = fetch_by_id(cur, con, meeting_id)
        if not m or not m.audio_transcript:
            return err_json("meeting not found or has no audio_transcript", status=404)
        
        # Parse transcript blocks
        try:
            blocks = json.loads(m.audio_transcript)
        except Exception as e:
            return err_json(f"Failed to parse audio_transcript: {e}")
        
        # Show speaker distribution before consolidation
        speaker_groups_before = {}
        for block in blocks:
            # Handle case where block might be a string instead of dict
            if isinstance(block, str):
                continue
            speaker_id = block.get("speaker_id")
            if speaker_id:
                if speaker_id not in speaker_groups_before:
                    speaker_groups_before[speaker_id] = []
                speaker_groups_before[speaker_id].append(block)
        
        # Apply consolidation
        consolidated_blocks = _consolidate_similar_speakers(blocks, similarity_threshold=SPEAKER_SIMILARITY_THRESHOLD)
        
        # Show speaker distribution after consolidation
        speaker_groups_after = {}
        for block in consolidated_blocks:
            # Handle case where block might be a string instead of dict
            if isinstance(block, str):
                continue
            speaker_id = block.get("speaker_id")
            if speaker_id:
                if speaker_id not in speaker_groups_after:
                    speaker_groups_after[speaker_id] = []
                speaker_groups_after[speaker_id].append(block)
        
        # Calculate similarity matrix for all speaker pairs
        similarity_matrix = {}
        speaker_ids = list(speaker_groups_before.keys())
        for i, sid1 in enumerate(speaker_ids):
            for sid2 in speaker_ids[i+1:]:
                similarity = _compute_speaker_similarity(
                    speaker_groups_before.get(sid1, []),
                    speaker_groups_before.get(sid2, [])
                )
                similarity_matrix[f"{sid1} vs {sid2}"] = similarity
        
        return ok_json({
            "ok": True,
            "meeting": m.brief(),
            "speakers_before_consolidation": {
                sid: len(blocks) for sid, blocks in speaker_groups_before.items()
            },
            "speakers_after_consolidation": {
                sid: len(blocks) for sid, blocks in speaker_groups_after.items()
            },
            "similarity_matrix": similarity_matrix,
            "consolidation_applied": len(speaker_groups_before) != len(speaker_groups_after)
        })

# ---------- Pending File Browser ----------
@app.get("/pending/list")
def list_pending_files():
    """List all pending files with metadata for Alfred workflow."""
    try:
        pendings = list_all_pendings()
        results = []
        
        for pending_path in pendings:
            try:
                pd = load_pending(pending_path)
                results.append({
                    "pending_path": str(pending_path),
                    "meeting_title": pd.get("meeting_title", ""),
                    "meeting_date": pd.get("meeting_date", ""),
                    "session_type": pd.get("session_type", ""),
                    "participants": pd.get("participants", []),
                    "transcript_snippet": pd.get("transcript_snippet", "")[:100] + "..." if len(pd.get("transcript_snippet", "")) > 100 else pd.get("transcript_snippet", ""),
                    "filename": pending_path.name
                })
            except Exception as e:
                logger.error("Failed to load pending file", extra={"pending_path": str(pending_path), "error": str(e)})
                results.append({
                    "pending_path": str(pending_path),
                    "error": str(e),
                    "filename": pending_path.name
                })
        
        return ok_json({
            "ok": True,
            "count": len(results),
            "pending_files": results
        })
    except Exception as e:
        logger.error("Failed to list pending files", extra={"error": str(e)})
        return err_json("Failed to list pending files", status=500, extra={"detail": str(e)})

@app.get("/pending/candidates")
def get_pending_candidates():
    """Get candidate meetings for a specific pending file."""
    pending_path = request.args.get("pending_path")
    if not pending_path:
        return err_json("Missing ?pending_path parameter")
    
    try:
        pd = load_pending(Path(pending_path))
        row, reason, debug_rows = find_best_candidate(pd)
        
        # Get top 10 candidates with detailed info
        candidates = []
        for i, candidate in enumerate(debug_rows[:10]):
            candidates.append({
                "rank": i + 1,
                "meeting_id": candidate.get("id"),
                "title": candidate.get("title", ""),
                "start_iso": ms_to_iso(candidate.get("start")),
                "end_iso": ms_to_iso(candidate.get("end")),
                "composite_score": candidate.get("composite", 0),
                "reason": candidate.get("reason", ""),
                "participants": candidate.get("participants", []),
                "has_transcript": bool(candidate.get("audio_transcript"))
            })
        
        return ok_json({
            "ok": True,
            "pending_file": {
                "meeting_title": pd.get("meeting_title", ""),
                "meeting_date": pd.get("meeting_date", ""),
                "session_type": pd.get("session_type", ""),
                "participants": pd.get("participants", [])
            },
            "best_match": {
                "meeting_id": row.id if row else None,
                "reason": reason,
                "confidence": debug_rows[0].get("composite", 0) if debug_rows else 0
            } if row else None,
            "candidates": candidates
        })
    except Exception as e:
        logger.error("Failed to get candidates", extra={"pending_path": pending_path, "error": str(e)})
        return err_json("Failed to get candidates", status=500, extra={"detail": str(e)})

# ---------- Main ----------
if __name__ == "__main__":
    logger.info("Starting Quill webhook server", extra={
        "host": HOST,
        "port": PORT,
        "notes_root": str(NOTES_ROOT),
        "quill_db_path": str(QUILL_DB_PATH)
    })
    
    # Ensure directories exist
    ensure_dir(SUMMARIES_ROOT)
    ensure_dir(TRANSCRIPTS_ROOT)
    
    # Check database health on startup
    db_health = check_database_health()
    if db_health["status"] == "healthy":
        logger.info("Database health check passed", extra=db_health)
    else:
        logger.warning("Database health check failed", extra=db_health)
    
    app.run(host=HOST, port=PORT, debug=False)