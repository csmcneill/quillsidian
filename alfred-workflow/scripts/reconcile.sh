#!/bin/bash
# Unified reconciliation script for Quillsidian
# Usage: reconcile.sh [mode] [args]
# Modes: compact, full, candidates

# Get the Quill directory (parent of this script)
QUILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$QUILL_DIR"

# Get the mode from first argument
MODE="${1:-help}"

# Function to show help
show_help() {
    echo "🎯 Quillsidian Reconciliation Script"
    echo ""
    echo "Usage: reconcile.sh [mode] [args]"
    echo ""
    echo "Modes:"
    echo "  compact <pending_path>     - Compact reconciliation for Alfred notifications"
    echo "  full <pending_path> <id>   - Full reconciliation with detailed output"
    echo "  candidates <pending_path>  - Get candidates for a pending file"
    echo "  help                       - Show this help message"
}

# Function to check if server is running
check_server() {
    if ! pgrep -f "quill_server.py" > /dev/null; then
        echo "❌ Server not running. Use 'quillsidian start' first."
        exit 1
    fi
}

# Function to get candidates from server
get_candidates() {
    local pending_path="$1"
    curl -s "http://localhost:5001/pending/candidates" -G --data-urlencode "pending_path=$pending_path"
}

# Function to reconcile with server
reconcile_with_server() {
    local pending_path="$1"
    local meeting_id="$2"
    curl -s -X POST "http://localhost:5001/reconcile/pick" \
        -H "Content-Type: application/json" \
        -d "{\"pending_path\": \"$pending_path\", \"meeting_id\": \"$meeting_id\"}"
}

# Function for compact reconciliation (Alfred notifications)
compact_reconcile() {
    local pending_path="$1"
    
    if [ -z "$pending_path" ]; then
        echo "❌ No pending file specified"
        exit 1
    fi
    
    check_server
    
    # Get candidates and extract the meeting ID
    candidates_output=$(get_candidates "$pending_path")
    # Try best_match first, then fall back to first candidate
    meeting_id=$(echo "$candidates_output" | jq -r '.best_match.meeting_id // .candidates[0].meeting_id // empty')
    
    if [ "$meeting_id" = "null" ] || [ -z "$meeting_id" ]; then
        echo "❌ No candidates found"
        exit 1
    fi
    
    # Run the reconciliation
    result=$(reconcile_with_server "$pending_path" "$meeting_id")
    
    # Format compact output
    if echo "$result" | jq -e '.ok' > /dev/null 2>&1; then
        meeting_title=$(echo "$result" | jq -r '.quill_title')
        echo "✅ Reconciled: $meeting_title"
    else
        echo "❌ Reconciliation failed"
    fi
}

# Function for full reconciliation (detailed output)
full_reconcile() {
    local pending_path="$1"
    local meeting_id="$2"
    
    if [ -z "$pending_path" ] || [ -z "$meeting_id" ]; then
        echo "❌ Missing arguments"
        echo "Usage: reconcile.sh full <pending_path> <meeting_id>"
        exit 1
    fi
    
    check_server
    
    # Run the reconciliation
    result=$(reconcile_with_server "$pending_path" "$meeting_id")
    
    # Parse and display results
    if command -v jq > /dev/null; then
        success=$(echo "$result" | jq -r '.ok')
        if [ "$success" = "true" ]; then
            echo "✅ Successfully reconciled!"
            echo "$result" | jq -r '
                "📄 Transcript: \(.saved_transcript)
   🆔 Meeting ID: \(.matched_id)
   📝 Quill Title: \(.quill_title)
   "'
        else
            echo "❌ Reconciliation failed:"
            echo "$result" | jq -r '.error'
        fi
    else
        echo "📄 Raw response (install 'jq' for better formatting):"
        echo "$result"
    fi
}


# Function to get candidates for a pending file
get_candidates_for_file() {
    local pending_path="$1"
    
    if [ -z "$pending_path" ]; then
        echo "❌ Missing pending file path"
        echo "Usage: reconcile.sh candidates <pending_path>"
        exit 1
    fi
    
    check_server
    
    # Get candidates from server
    response=$(get_candidates "$pending_path")
    
    if [ $? -ne 0 ]; then
        echo "❌ Failed to connect to server"
        exit 1
    fi
    
    # Parse and display results
    if command -v jq > /dev/null; then
        echo "🎯 Candidates for pending file:"
        echo ""
        
        # Show pending file info
        echo "$response" | jq -r '.pending_file | 
            "📄 \(.meeting_title) (\(.meeting_date))
   👥 \(.participants | join(", "))
   📋 Type: \(.session_type)
   "'
        
        # Show best match if available
        best_match=$(echo "$response" | jq -r '.best_match')
        if [ "$best_match" != "null" ]; then
            echo "🏆 Best Match:"
            echo "$response" | jq -r '.best_match | 
                "   🎯 \(.meeting_id)
   📊 Confidence: \(.confidence)
   🔍 Reason: \(.reason)
   "'
        fi
        
        echo "📋 All Candidates:"
        echo "$response" | jq -r '.candidates[] | 
            "\(.rank). \(.title)
   🆔 \(.meeting_id)
   📊 Score: \(.composite_score) | \(.reason)
   👥 \(.participants)
   📝 Has transcript: \(.has_transcript)
   "'
    else
        echo "📄 Raw response (install 'jq' for better formatting):"
        echo "$response"
    fi
}

# Main command router
case "$MODE" in
    "compact")
        compact_reconcile "$2"
        ;;
    "full")
        full_reconcile "$2" "$3"
        ;;
    "candidates")
        get_candidates_for_file "$2"
        ;;
    "help"|"")
        show_help
        ;;
    *)
        echo "❌ Unknown mode: $MODE"
        echo ""
        show_help
        exit 1
        ;;
esac
