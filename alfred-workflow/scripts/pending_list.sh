#!/bin/bash
# Alfred Grid View: List Pending Files
# This script is specifically for Alfred's Grid View component

# Get the Quill directory (parent of this script)
QUILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$QUILL_DIR"

# Check if server is running
if ! pgrep -f "quill_server.py" > /dev/null; then
    echo '{"items": [{"title": "❌ Server not running", "subtitle": "Use quillsidian start first", "icon": "❌"}]}'
    exit 1
fi

# Check if server is responding
if ! curl -s http://localhost:5001/health > /dev/null; then
    echo '{"items": [{"title": "❌ Server not responding", "subtitle": "Try quillsidian reboot", "icon": "❌"}]}'
    exit 1
fi

# Get pending files from server
response=$(curl -s "http://localhost:5001/pending/list")
if [ $? -ne 0 ]; then
    echo '{"items": [{"title": "❌ Failed to connect to server", "subtitle": "Check if Quillsidian server is running", "icon": "❌"}]}'
    exit 1
fi

# Parse and format for Alfred Grid View
if command -v jq > /dev/null; then
    # Use jq to build the entire JSON structure
    echo "$response" | jq -r '
        {
            "items": [
                .pending_files[] | {
                    "title": ("📄 " + .meeting_title),
                    "subtitle": ("📅 " + .meeting_date + " | 👥 " + (.participants | join(", "))),
                    "arg": .pending_path,
                    "icon": "📄"
                }
            ]
        }'
else
    echo '{"items": [{"title": "⚠️ jq not found", "subtitle": "Install jq for full functionality", "icon": "⚠️"}]}'
fi
