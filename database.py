#!/usr/bin/env python3
"""
Database connection management for Quill webhook server
"""

import sqlite3
import logging
from contextlib import contextmanager
from typing import Generator, Optional, Dict, List, Any
from pathlib import Path

from config import config
from logging_config import log_database_operation

logger = logging.getLogger(__name__)

class DatabaseError(Exception):
    """Custom exception for database operations."""
    pass

@contextmanager
def get_db_connection() -> Generator[sqlite3.Connection, None, None]:
    """Context manager for database connections with proper error handling."""
    conn = None
    try:
        conn = sqlite3.connect(str(config.quill_db_path))
        conn.row_factory = sqlite3.Row
        yield conn
    except sqlite3.Error as e:
        log_database_operation("connect", "database", False, error=str(e))
        logger.error(f"Database connection failed: {e}")
        raise DatabaseError(f"Failed to connect to database: {e}")
    except OSError as e:
        log_database_operation("connect", "database", False, error=str(e))
        logger.error(f"Database file access error: {e}")
        raise DatabaseError(f"Database file not accessible: {e}")
    finally:
        if conn:
            try:
                conn.close()
            except sqlite3.Error as e:
                logger.warning(f"Error closing database connection: {e}")

def execute_query(query: str, params: tuple = ()) -> List[sqlite3.Row]:
    """Execute a query with proper error handling."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return cursor.fetchall()
    except sqlite3.Error as e:
        log_database_operation("query", "database", False, error=str(e))
        logger.error(f"Database query failed: {e}")
        logger.error(f"Query: {query}")
        logger.error(f"Params: {params}")
        raise DatabaseError(f"Query execution failed: {e}")

def execute_single_query(query: str, params: tuple = ()) -> Optional[sqlite3.Row]:
    """Execute a query that returns a single row."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return cursor.fetchone()
    except sqlite3.Error as e:
        log_database_operation("query", "database", False, error=str(e))
        logger.error(f"Database query failed: {e}")
        logger.error(f"Query: {query}")
        logger.error(f"Params: {params}")
        raise DatabaseError(f"Query execution failed: {e}")

def execute_write_query(query: str, params: tuple = ()) -> int:
    """Execute a write query and return number of affected rows."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            conn.commit()
            rows_affected = cursor.rowcount
            log_database_operation("write", "database", True, rows_affected=rows_affected)
            return rows_affected
    except sqlite3.Error as e:
        log_database_operation("write", "database", False, error=str(e))
        logger.error(f"Database write failed: {e}")
        logger.error(f"Query: {query}")
        logger.error(f"Params: {params}")
        raise DatabaseError(f"Write operation failed: {e}")

def check_database_health() -> Dict[str, Any]:
    """Check database health and return status information."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            # Check if database exists and is accessible
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='Meeting'")
            meeting_table_exists = cursor.fetchone() is not None
            
            # Count meetings
            cursor.execute("SELECT COUNT(*) FROM Meeting WHERE deleteDate IS NULL")
            meeting_count = cursor.fetchone()[0]
            
            # Count ContactMeeting entries
            cursor.execute("SELECT COUNT(*) FROM ContactMeeting")
            contact_meeting_count = cursor.fetchone()[0]
            
            # Check for recent meetings
            cursor.execute("""
                SELECT COUNT(*) FROM Meeting 
                WHERE deleteDate IS NULL 
                AND start > strftime('%s', 'now', '-7 days') * 1000
            """)
            recent_meetings = cursor.fetchone()[0]
            
            return {
                "status": "healthy",
                "database_path": str(config.quill_db_path),
                "meeting_table_exists": meeting_table_exists,
                "total_meetings": meeting_count,
                "contact_meeting_entries": contact_meeting_count,
                "recent_meetings_7d": recent_meetings,
                "error": None
            }
            
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        return {
            "status": "unhealthy",
            "database_path": str(config.quill_db_path),
            "error": str(e)
        }

def get_meeting_by_id(meeting_id: str) -> Optional[sqlite3.Row]:
    """Get a meeting by ID with proper error handling."""
    query = """
        SELECT id, title, participants, start, end, audio_transcript
        FROM Meeting 
        WHERE deleteDate IS NULL AND id = ?
        LIMIT 1
    """
    try:
        return execute_single_query(query, (meeting_id,))
    except DatabaseError:
        return None

def get_meetings_in_window(start_ms: int, end_ms: int, limit: int = 600) -> List[sqlite3.Row]:
    """Get meetings in a time window with proper error handling."""
    query = """
        SELECT id, title, participants, start, end, audio_transcript
        FROM Meeting
        WHERE deleteDate IS NULL AND NOT (end < ? OR start > ?)
        ORDER BY start ASC 
        LIMIT ?
    """
    try:
        return execute_query(query, (start_ms, end_ms, limit))
    except DatabaseError:
        return []

def get_contact_meeting_speakers(meeting_id: str) -> Dict[str, str]:
    """Get speaker mappings from ContactMeeting table with proper error handling."""
    query = """
        SELECT speaker_id, suggested_name, contact_id
        FROM ContactMeeting 
        WHERE meeting_id = ? AND suggested_name IS NOT NULL
    """
    try:
        rows = execute_query(query, (meeting_id,))
        speakers = {}
        
        for row in rows:
            speaker_id = row[0]
            suggested_name = row[1]
            contact_id = row[2]
            
            # If we have a contact_id, try to get the actual contact name
            if contact_id:
                contact_query = "SELECT name FROM Contact WHERE id = ?"
                contact_row = execute_single_query(contact_query, (contact_id,))
                if contact_row:
                    speakers[speaker_id] = contact_row[0]
                else:
                    speakers[speaker_id] = suggested_name
            else:
                speakers[speaker_id] = suggested_name
                
        return speakers
        
    except DatabaseError as e:
        logger.warning(f"Failed to fetch ContactMeeting speakers for {meeting_id}: {e}")
        return {}
