#!/bin/bash
# Alfred Workflow: Quillsidian Server Manager
# Usage: quill_manager.sh [command]
# Commands: start, stop, status, health, logs, reboot, test

# Get the Quill directory (parent of this script)
QUILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$QUILL_DIR"

# Get the command from first argument
COMMAND="${1:-help}"

# Function to show help
show_help() {
    cat << 'EOF'
🚀 Quillsidian Server Manager

Usage: quillsidian [command]

Commands:
  start     - Start the Quillsidian webhook server
  stop      - Stop the Quillsidian webhook server
  status    - Check if server is running
  health    - Detailed health check
  logs      - View recent server logs
  reboot    - Restart the server cleanly
  reconcile - Process all pending files automatically
  list      - Browse pending files interactively
  test      - Run the test suite
  help      - Show this help message
EOF
}

# Function to start server
start_server() {
    # Check if virtual environment exists
    if [ ! -d ".venv" ]; then
        echo "❌ Virtual environment not found"
        exit 1
    fi

    # Check if server is already running
    if pgrep -f "quill_server.py" > /dev/null; then
        echo "⚠️ Server already running (PID: $(pgrep -f 'quill_server.py'))"
        exit 0
    fi

    # Use venv's Python directly (more reliable than activation in Alfred context)
    VENV_PYTHON=".venv/bin/python3"
    
    # Check if required packages are installed
    if ! "$VENV_PYTHON" -c "import flask" 2>/dev/null; then
        echo "📦 Installing Flask..."
        "$VENV_PYTHON" -m pip install flask
    fi

    # Start the server in background using venv's Python
    nohup "$VENV_PYTHON" quill_server.py > /tmp/quill_server.log 2>&1 &

    # Wait a moment for server to start
    sleep 2

    # Check if server started successfully
    if pgrep -f "quill_server.py" > /dev/null; then
        PID=$(pgrep -f "quill_server.py")
        echo "✅ Server started (PID: $PID)"
    else
        echo "❌ Failed to start server"
        exit 1
    fi
}

# Function to stop server
stop_server() {
    if pgrep -f "quill_server.py" > /dev/null; then
        PIDS=$(pgrep -f "quill_server.py")
        
        for PID in $PIDS; do
            kill $PID
        done
        
        sleep 2
        
        # Check if any processes are still running
        if pgrep -f "quill_server.py" > /dev/null; then
            pkill -9 -f "quill_server.py"
        fi
        
        echo "✅ Server stopped"
    else
        echo "ℹ️ No server processes found"
    fi
}

# Function to check status
check_status() {
    if pgrep -f "quill_server.py" > /dev/null; then
        PID=$(pgrep -f "quill_server.py")
        echo "✅ Server running (PID: $PID)"
    else
        echo "❌ Server not running"
    fi
}

# Function to health check
health_check() {
    if pgrep -f "quill_server.py" > /dev/null; then
        if curl -s http://localhost:5001/health > /dev/null; then
            echo "✅ Server healthy"
        else
            echo "❌ Server not responding"
        fi
    else
        echo "❌ Server not running"
    fi
}

# Function to view logs
view_logs() {
    # Check both log locations
    SERVER_LOG="/tmp/quill_server.log"
    PROJECT_LOG="$QUILL_DIR/server.log"
    
    echo "📋 Recent server logs:"
    echo ""
    
    # Show server log if it exists
    if [ -f "$SERVER_LOG" ]; then
        echo "📄 Server output log ($SERVER_LOG):"
        tail -20 "$SERVER_LOG" | while IFS= read -r line; do
            if echo "$line" | grep -qiE "error|exception|failed|critical"; then
                echo "❌ $line"
            elif echo "$line" | grep -qiE "warning"; then
                echo "⚠️  $line"
            else
                echo "   $line"
            fi
        done
        echo ""
    fi
    
    # Show project log if it exists
    if [ -f "$PROJECT_LOG" ]; then
        echo "📄 Project log ($PROJECT_LOG):"
        tail -20 "$PROJECT_LOG" | while IFS= read -r line; do
            if echo "$line" | grep -qiE "error|exception|failed|critical"; then
                echo "❌ $line"
            elif echo "$line" | grep -qiE "warning"; then
                echo "⚠️  $line"
            else
                echo "   $line"
            fi
        done
    fi
    
    if [ ! -f "$SERVER_LOG" ] && [ ! -f "$PROJECT_LOG" ]; then
        echo "ℹ️ No log files found"
        echo "   Server logs appear after starting the server"
    fi
}

# Function to reboot server
reboot_server() {
    # Stop the server first
    stop_server
    
    # Start the server
    start_server
}

# Function to run tests
run_tests() {
    # Check if virtual environment exists
    if [ ! -d ".venv" ]; then
        echo "❌ Virtual environment not found"
        exit 1
    fi
    
    # Use venv's Python directly
    VENV_PYTHON=".venv/bin/python3"
    
    # Run tests
    if [ -f "test_improvements.py" ]; then
        "$VENV_PYTHON" test_improvements.py
    elif [ -f "test_consolidation.py" ]; then
        "$VENV_PYTHON" test_consolidation.py
    else
        echo "❌ No test files found"
        exit 1
    fi
}

# Function to reconcile pending files
reconcile_pending() {
    # Check if server is running
    if ! pgrep -f "quill_server.py" > /dev/null; then
        echo "❌ Server not running. Use 'quillsidian start' first."
        exit 1
    fi
    
    # Check if server is responding
    if ! curl -s http://localhost:5001/health > /dev/null; then
        echo "❌ Server not responding. Try 'quillsidian reboot'."
        exit 1
    fi
    
    # Call the reconcile endpoint
    response=$(curl -s -X POST http://localhost:5001/reconcile/auto)
    
    # Check if curl was successful
    if [ $? -ne 0 ]; then
        echo "❌ Failed to connect to server"
        exit 1
    fi
    
    # Parse and display compact results
    if command -v jq > /dev/null; then
        # Use jq to parse the response
        success_count=$(echo "$response" | jq -r '.results | map(select(.created_transcript != null)) | length')
        total_count=$(echo "$response" | jq -r '.results | length')
        
        # Compact notification format
        if [ "$success_count" -eq 0 ]; then
            echo "🔄 Reconciled: $success_count/$total_count files"
        else
            echo "✅ Reconciled: $success_count/$total_count files"
        fi
        
        # Show any errors in compact format
        error_count=$(echo "$response" | jq -r '.results | map(select(.error != null)) | length')
        if [ "$error_count" -gt 0 ]; then
            echo "⚠️ $error_count errors"
        fi
    else
        # Fallback without jq - just show success/failure
        if echo "$response" | grep -q '"created_transcript"'; then
            echo "✅ Reconciliation completed"
        else
            echo "🔄 No files reconciled"
        fi
    fi
}

# Function to browse pending files
browse_pending() {
    echo "📄 Use 'quillsidian list' in Alfred to browse pending files interactively"
    echo "   This opens the interactive pending file browser where you can:"
    echo "   • View all pending files"
    echo "   • See candidate meetings for each file"
    echo "   • Select specific meetings to reconcile"
}

# Main command router
case "$COMMAND" in
    "start")
        start_server
        ;;
    "stop")
        stop_server
        ;;
    "status")
        check_status
        ;;
    "health")
        health_check
        ;;
    "logs")
        view_logs
        ;;
    "reboot")
        reboot_server
        ;;
    "reconcile")
        reconcile_pending
        ;;
    "list")
        browse_pending
        ;;
    "test")
        run_tests
        ;;
    "help"|"")
        show_help
        ;;
    *)
        echo "❌ Unknown command: $COMMAND"
        echo ""
        show_help
        exit 1
        ;;
esac
