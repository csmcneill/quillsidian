#!/bin/bash

# Get the query from Alfred
query="$1"

# Handle null/empty query
if [[ "$query" == "(null)" || -z "$query" ]]; then
    query=""
fi

# Define commands with title, subtitle, arg, and icon
declare -a commands=(
    "start|Start the Quillsidian webhook server|Start the server to begin processing Quill webhooks|🚀"
    "stop|Stop the Quillsidian webhook server|Stop the server and end webhook processing|🛑"
    "status|Check if the Quillsidian webhook server is running|Show current server status and process information|📊"
    "health|Detailed health check of the Quillsidian webhook server|Perform a comprehensive health check and show detailed status|🏥"
    "logs|View recent Quillsidian webhook server logs|Show the last 20 lines of server logs|📋"
    "reboot|Cleanly restart the Quillsidian webhook server|Stop and restart the server with a clean state|🔄"
    "reconcile|Reconcile all pending files automatically|Process all pending JSON files and match with transcripts|🔄"
    "list|Browse pending files interactively|Select pending files and choose matching meetings|📄"
    "test|Run the Quillsidian test suite|Execute all tests to verify server functionality|🧪"
    "help|Show the Quillsidian help message|Display available commands and usage information|❓"
)

# Start JSON output
echo '{"items": ['

first=true
for cmd_pair in "${commands[@]}"; do
    IFS='|' read -r arg title subtitle icon <<< "$cmd_pair"
    
    # Filter based on query (case-insensitive)
    if [[ -z "$query" || "$arg" == *"$query"* || "$title" == *"$query"* ]]; then
        # Convert to lowercase for comparison
        query_lower=$(echo "$query" | tr '[:upper:]' '[:lower:]')
        arg_lower=$(echo "$arg" | tr '[:upper:]' '[:lower:]')
        title_lower=$(echo "$title" | tr '[:upper:]' '[:lower:]')
        
        if [[ -z "$query" || "$arg_lower" == *"$query_lower"* || "$title_lower" == *"$query_lower"* ]]; then
            if [ "$first" = true ]; then
                first=false
            else
                echo ","
            fi
            
            echo "  {"
            echo "    \"title\": \"$title\","
            echo "    \"subtitle\": \"$subtitle\","
            echo "    \"arg\": \"$arg\","
            echo "    \"icon\": \"$icon\""
            echo "  }"
        fi
    fi
done

# Close JSON output
echo "]}"
