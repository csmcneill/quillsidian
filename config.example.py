#!/usr/bin/env python3
"""
Configuration template for Quill webhook server

INSTRUCTIONS:
1. Copy this file to config.py: cp config.example.py config.py
2. Edit config.py with your specific paths and settings
3. config.py is gitignored and won't be committed to the repository

Alternatively, you can set environment variables to override these defaults.
See README.md for environment variable names.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Set, Literal
import os

# Type definitions for better type safety
SessionType = Literal["1-on-1", "internal-sync", "external-sync", "note-to-self", "default"]

@dataclass
class QuillConfig:
    """Configuration for the Quill webhook server."""
    
    # Paths - Update these to match your system
    # Environment variables: QUILL_NOTES_ROOT, QUILL_DB_PATH
    notes_root: Path = Path(os.getenv("QUILL_NOTES_ROOT", "/path/to/your/quillsidian/project"))
    quill_db_path: Path = Path(os.getenv("QUILL_DB_PATH", os.path.expanduser("~/Library/Application Support/Quill/quill.db")))
    
    # Server settings
    # Environment variables: QUILL_PORT, QUILL_HOST
    port: int = int(os.getenv("QUILL_PORT", "5001"))
    host: str = os.getenv("QUILL_HOST", "0.0.0.0")
    
    # Behavior settings
    # Environment variables: QUILL_AUTO_RECONCILE, QUILL_WINDOW_HOURS
    auto_reconcile_on_summary: bool = os.getenv("QUILL_AUTO_RECONCILE", "true").lower() == "true"
    window_hours: int = int(os.getenv("QUILL_WINDOW_HOURS", "36"))
    
    # User identity - Update with your name and common aliases
    # Environment variables: QUILL_CANONICAL_NAME, QUILL_ALIASES (comma-separated)
    canonical_name: str = os.getenv("QUILL_CANONICAL_NAME", "Your Name")
    aliases: Set[str] = None
    
    # File settings
    pending_suffix: str = ".pending.json"
    colon_for_filename: str = "ː"
    
    # Strip tokens from transcript text
    strip_tokens: Dict[str, str] = None
    
    # Sources that typically mean "local speaker"
    # These help identify which speaker is you in the transcript
    local_sources: Set[str] = None
    
    # Speaker attribution settings
    enable_speaker_consolidation: bool = True
    speaker_similarity_threshold: float = 0.4
    
    def __post_init__(self):
        """Set default values for mutable fields."""
        # Parse aliases from environment variable or use defaults
        if self.aliases is None:
            env_aliases = os.getenv("QUILL_ALIASES", "")
            if env_aliases:
                self.aliases = {a.strip().lower() for a in env_aliases.split(",") if a.strip()}
            else:
                # Default aliases - customize these for your name
                self.aliases = {"me", "your-first-name"}
        
        if self.strip_tokens is None:
            self.strip_tokens = {"<SNIP>": ""}
        
        if self.local_sources is None:
            self.local_sources = {"mic", "local", "local-user"}
    
    @property
    def summaries_root(self) -> Path:
        """Path where meeting summaries are saved."""
        env_path = os.getenv("QUILL_SUMMARIES_ROOT")
        if env_path:
            return Path(env_path)
        # Default: summaries directory relative to notes_root
        return self.notes_root.parent.parent / "3. Resources" / "Summaries"
    
    @property
    def transcripts_root(self) -> Path:
        """Path where meeting transcripts are saved."""
        env_path = os.getenv("QUILL_TRANSCRIPTS_ROOT")
        if env_path:
            return Path(env_path)
        # Default: transcripts directory relative to notes_root
        return self.notes_root.parent.parent / "3. Resources" / "Transcripts"
    
    @property
    def overrides_dir(self) -> Path:
        """Directory for manual speaker label overrides."""
        return self.notes_root / ".quill_overrides"

# Confidence thresholds for different session types
# These determine how strict the matching algorithm is for each meeting type
CONFIDENCE_THRESHOLDS: Dict[SessionType, float] = {
    "1-on-1": 0.45,
    "internal-sync": 0.42,
    "external-sync": 0.35,
    "note-to-self": 0.35,
    "default": 0.40,
}

# Weight table for scoring
# Determines how much weight to give participant overlap, title similarity, and time proximity
WEIGHT_TABLE: Dict[SessionType, Dict[str, float]] = {
    "1-on-1": {"overlap": 0.70, "title": 0.15, "time": 0.15},
    "internal-sync": {"overlap": 0.65, "title": 0.20, "time": 0.15},
    "external-sync": {"overlap": 0.75, "title": 0.10, "time": 0.15},
    "note-to-self": {"overlap": 0.60, "title": 0.10, "time": 0.30},
    "default": {"overlap": 0.60, "title": 0.25, "time": 0.15},
}

# Default configuration instance
config = QuillConfig()
