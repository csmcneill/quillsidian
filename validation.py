#!/usr/bin/env python3
"""
Input validation for Quill webhook server
"""

import re
from datetime import datetime
from typing import Optional, Tuple, List
from pathlib import Path

def validate_meeting_id(meeting_id: str) -> bool:
    """Validate Quill meeting ID format (UUID)."""
    if not meeting_id or not isinstance(meeting_id, str):
        return False
    # Quill uses UUID format
    uuid_pattern = re.compile(
        r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', 
        re.IGNORECASE
    )
    return bool(uuid_pattern.match(meeting_id))

def validate_date_format(date_str: str) -> bool:
    """Validate date format YYYY-MM-DD."""
    if not date_str or not isinstance(date_str, str):
        return False
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return True
    except ValueError:
        return False

def validate_timestamp_ms(timestamp: Optional[int]) -> bool:
    """Validate millisecond timestamp."""
    if timestamp is None:
        return True  # None is valid
    if not isinstance(timestamp, int):
        return False
    # Reasonable range: 2000-01-01 to 2030-01-01
    min_ms = 946684800000  # 2000-01-01
    max_ms = 1893456000000  # 2030-01-01
    return min_ms <= timestamp <= max_ms

def validate_session_type(session_type: str) -> bool:
    """Validate session type."""
    valid_types = {"1-on-1", "internal-sync", "external-sync", "note-to-self", "default"}
    return session_type in valid_types

def validate_participants(participants: List[str]) -> bool:
    """Validate participants list."""
    if not isinstance(participants, list):
        return False
    return all(isinstance(p, str) and p.strip() for p in participants)

def validate_file_path(path: Path) -> bool:
    """Validate file path exists and is accessible."""
    try:
        return path.exists() and path.is_file()
    except (OSError, ValueError):
        return False

def validate_directory_path(path: Path) -> bool:
    """Validate directory path exists and is accessible."""
    try:
        return path.exists() and path.is_dir()
    except (OSError, ValueError):
        return False

def sanitize_filename(filename: str) -> str:
    """Sanitize filename for safe filesystem operations."""
    if not filename:
        return "untitled"
    
    # Replace problematic characters with visually similar alternatives
    # that are safe for macOS filesystem
    char_replacements = [
        (":", "ː"),      # colon → modifier letter colon
        ("–", "–"),      # en dash → en dash (keep consistent)
        ("—", "–"),      # em dash → en dash
        ("/", "-"),      # solidus → hyphen
        ("\\", "-"),     # reverse solidus → hyphen
        ("*", "∗"),      # asterisk → asterisk operator
        ("?", "？"),     # question mark → fullwidth question mark
        ('"', "'"),      # double quotes → single quotes
        ("'", "'"),      # smart single quotes → regular single quotes
        ("<", "("),      # less-than sign → left parenthesis
        (">", ")"),      # greater-than sign → right parenthesis
        ("|", "-"),      # vertical line → hyphen
    ]
    
    # Apply character replacements
    for old_char, new_char in char_replacements:
        filename = filename.replace(old_char, new_char)
    
    # Replace any remaining unsafe characters with en dash
    unsafe_chars = r'[\\/:*?"<>|]'
    filename = re.sub(unsafe_chars, "–", filename)
    
    # Normalize whitespace
    filename = re.sub(r'\s+', ' ', filename).strip()
    
    # Limit length
    if len(filename) > 200:
        filename = filename[:200]
    
    return filename or "untitled"

def validate_webhook_payload(payload: dict) -> Tuple[bool, Optional[str]]:
    """Validate webhook payload structure."""
    required_fields = ["summary_markdown", "meeting_title", "meeting_date"]
    
    for field in required_fields:
        if field not in payload:
            return False, f"Missing required field: {field}"
        
        if not payload[field]:
            return False, f"Empty required field: {field}"
    
    # Validate date format
    if not validate_date_format(payload["meeting_date"]):
        return False, "Invalid date format. Expected YYYY-MM-DD"
    
    # Validate optional fields if present
    if "quill_meeting_id" in payload and payload["quill_meeting_id"]:
        if not validate_meeting_id(payload["quill_meeting_id"]):
            return False, "Invalid meeting ID format"
    
    if "quill_start_ms" in payload and payload["quill_start_ms"] is not None:
        if not validate_timestamp_ms(payload["quill_start_ms"]):
            return False, "Invalid start timestamp"
    
    if "quill_end_ms" in payload and payload["quill_end_ms"] is not None:
        if not validate_timestamp_ms(payload["quill_end_ms"]):
            return False, "Invalid end timestamp"
    
    return True, None
