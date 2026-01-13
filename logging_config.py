#!/usr/bin/env python3
"""
Enhanced logging configuration for Quill webhook server
"""

import logging
import json
from datetime import datetime
from typing import Any, Dict, Optional, List
from pathlib import Path

class StructuredFormatter(logging.Formatter):
    """Custom formatter for structured logging."""
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record with structured data."""
        # Extract structured data from extra fields
        structured_data = {}
        for key, value in record.__dict__.items():
            if key not in ['name', 'msg', 'args', 'levelname', 'levelno', 'pathname', 
                          'filename', 'module', 'lineno', 'funcName', 'created', 
                          'msecs', 'relativeCreated', 'thread', 'threadName', 
                          'processName', 'process', 'getMessage', 'exc_info', 
                          'exc_text', 'stack_info']:
                structured_data[key] = value
        
        # Base log message
        log_entry = {
            'timestamp': datetime.fromtimestamp(record.created).isoformat(),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
            'module': record.module,
            'function': record.funcName,
            'line': record.lineno,
        }
        
        # Add structured data if present
        if structured_data:
            log_entry['data'] = structured_data
        
        # Add exception info if present
        if record.exc_info:
            log_entry['exception'] = self.formatException(record.exc_info)
        
        return json.dumps(log_entry, ensure_ascii=False)

def setup_logging(log_level: str = "INFO", log_file: Optional[Path] = None) -> None:
    """Setup enhanced logging configuration."""
    
    # Create logger
    logger = logging.getLogger()
    logger.setLevel(getattr(logging, log_level.upper()))
    
    # Clear existing handlers
    logger.handlers.clear()
    
    # Console handler with structured formatting
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_formatter = StructuredFormatter()
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)
    
    # File handler if specified
    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(logging.DEBUG)
        file_formatter = StructuredFormatter()
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)

def log_meeting_processed(
    meeting_id: str, 
    participants: List[str], 
    success: bool, 
    error: Optional[str] = None
) -> None:
    """Log meeting processing result."""
    logger = logging.getLogger(__name__)
    logger.info(
        "Meeting processed",
        extra={
            "meeting_id": meeting_id,
            "participant_count": len(participants),
            "participants": participants,
            "success": success,
            "error": error
        }
    )

def log_webhook_received(
    meeting_title: str, 
    meeting_date: str, 
    payload_size: int
) -> None:
    """Log webhook reception."""
    logger = logging.getLogger(__name__)
    logger.info(
        "Webhook received",
        extra={
            "meeting_title": meeting_title,
            "meeting_date": meeting_date,
            "payload_size": payload_size
        }
    )

def log_database_operation(
    operation: str, 
    table: str, 
    success: bool, 
    rows_affected: Optional[int] = None,
    error: Optional[str] = None
) -> None:
    """Log database operations."""
    logger = logging.getLogger(__name__)
    logger.info(
        "Database operation",
        extra={
            "operation": operation,
            "table": table,
            "success": success,
            "rows_affected": rows_affected,
            "error": error
        }
    )

def log_speaker_mapping(
    meeting_id: str, 
    speaker_count: int, 
    mapped_speakers: Dict[str, str]
) -> None:
    """Log speaker mapping results."""
    logger = logging.getLogger(__name__)
    logger.info(
        "Speaker mapping completed",
        extra={
            "meeting_id": meeting_id,
            "speaker_count": speaker_count,
            "mapped_speakers": mapped_speakers
        }
    )

def log_matching_result(
    meeting_title: str,
    session_type: str,
    score: float,
    threshold: float,
    matched: bool,
    reason: str
) -> None:
    """Log meeting matching results."""
    logger = logging.getLogger(__name__)
    logger.info(
        "Meeting matching result",
        extra={
            "meeting_title": meeting_title,
            "session_type": session_type,
            "score": score,
            "threshold": threshold,
            "matched": matched,
            "reason": reason
        }
    )
