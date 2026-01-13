# Alfred Workflow: Quillsidian Server Manager

A comprehensive Alfred workflow for managing your Quillsidian webhook server without touching Terminal.

## 🚀 Features

- **Start Server**: `quillsidian start` or `qs start` - Start the Quillsidian webhook server
- **Stop Server**: `quillsidian stop` or `qs stop` - Stop the server cleanly
- **Check Status**: `quillsidian status` or `qs status` - Quick status check
- **Health Check**: `quillsidian health` or `qs health` - Detailed health information
- **View Logs**: `quillsidian logs` or `qs logs` - View recent server logs
- **Reboot Server**: `quillsidian reboot` or `qs reboot` - Clean restart
- **Run Tests**: `quillsidian test` or `qs test` - Run the test suite
- **Reconcile Pending**: `quillsidian reconcile` or `qs reconcile` - Auto-reconcile all pending files
- **Browse Pending**: `quillsidian list` or `qs list` - Interactive pending file browser
- **Help**: `quillsidian help` or `qs help` - Show available commands

## 📦 Installation

1. **Import into Alfred**:
   - Open Alfred Preferences
   - Go to Workflows tab
   - Click the + button → Import
   - Select the `Quillsidian Server Manager.alfredworkflow` file
   - Enable the workflow
   
   **Note**: The workflow file is not included in this repository. Import the workflow separately from your Alfred workflow export.

2. **Test the workflow**:
   - Try `quillsidian start` to start the server
   - Try `quillsidian status` to check if it's running

## 🎯 Usage

### Start the Server
```
quillsidian start
```
- Starts the Quillsidian webhook server in the background
- Shows PID, port, and health URL
- Logs output to `/tmp/quill_server.log`

### Check Status
```
quillsidian status
```
- Shows if server is running
- Displays PID and port
- Attempts health check if curl is available

### Stop the Server
```
quillsidian stop
```
- Gracefully stops the server
- Force kills if necessary
- Confirms server is stopped

### Health Check
```
quillsidian health
```
- Detailed health information
- Database status and meeting counts
- Server response validation

### View Logs
```
quillsidian logs
```
- Opens logs in a Text View node for easy review
- Shows recent server activity
- Displays log file location
- Useful for debugging and monitoring server activity

### Reboot Server
```
quillsidian reboot
```
- Stops and restarts the server cleanly
- Ensures fresh state
- Useful for applying configuration changes

### Run Tests
```
quillsidian test
```
- Runs the test suite
- Validates all improvements
- Shows test results

### Reconcile Pending Files
```
quillsidian reconcile
```
- Automatically reconciles all pending JSON files
- Matches with Quill database transcripts
- Shows compact results in Alfred notifications

### Browse Pending Files
```
quillsidian list
```
- Interactive browser for pending files
- Grid View shows all pending summaries with refined UI
- Select files to see candidate meetings
- Choose best match for reconciliation
- Improved visual presentation for better workflow

### Show Help
```
quillsidian help
```
- Displays all available commands
- Shows usage information
- Quick reference guide

## 🔧 Configuration

The workflow uses your existing Quill server configuration:
- Virtual environment: `.venv/`
- Server port: 5001
- Log file: `/tmp/quill_server.log`
- Health endpoint: `http://localhost:5001/health`

### Environment Variables
The workflow automatically detects the Quillsidian directory by finding the scripts directory. No manual configuration needed.

### Workflow Details
- **Bundle ID**: `com.obsidian.quill`
- **Category**: Tools
- **Keywords**: `quillsidian` and `qs` (both trigger the same workflow)
- **Queue Delay**: 3 seconds for better performance
- **Script Path**: Automatically detected (scripts are in `alfred-workflow/scripts/`)
- **Grid View**: Interactive file browser with loading text "Fetching transcripts..."
- **Audio Feedback**: Pop sound on completion

### Setup Notes
The workflow scripts automatically detect the Quillsidian project directory by navigating up from the scripts folder. Make sure the workflow file is imported into Alfred and the scripts directory structure is preserved.

## 🛠️ Troubleshooting

### Server Won't Start
- Check if virtual environment exists: `ls -la .venv/`
- Verify Flask is installed: `source .venv/bin/activate && pip list | grep flask`
- Check logs: `quillsidian logs`

### Health Check Fails
- Server may be starting up (wait 2-3 seconds)
- Check if port 5001 is available: `lsof -i :5001`
- Verify server is running: `quillsidian status`

### Permission Issues
- Make sure scripts are executable: `chmod +x scripts/*.sh`
- Check Alfred has necessary permissions

### Reconciliation Issues
- Ensure server is running: `quillsidian status`
- Check server health: `quillsidian health`
- View logs for errors: `quillsidian logs`

## 📁 File Structure

```
alfred-workflow/
├── Quillsidian Server Manager.alfredworkflow  # Alfred workflow file (import separately)
├── README.md                           # This file
├── quillsidian_commands.json           # Command definitions for autocomplete
├── quillsidian_commands.csv            # CSV version for List Filter
└── scripts/
    ├── quillsidian.sh                  # Unified server management script
    ├── script_filter.sh                # Script Filter for autocomplete
    ├── reconcile.sh                    # Unified reconciliation script
    └── pending_list.sh                 # Grid View script for pending files
```

**Note**: The `.alfredworkflow` file is not included in this repository. The workflow should be imported separately into Alfred. The scripts and configuration files in this directory are included for reference and can be used when setting up the workflow.

## 🎉 Benefits

- ✅ **No more Terminal typing** - Everything from Alfred
- ✅ **Autocomplete functionality** - Type `quillsidian` or `qs` and see all options
- ✅ **Smart filtering** - Type `quillsidian s` to see start, stop, status
- ✅ **Quick status checks** - See if server is running instantly
- ✅ **Health monitoring** - Detailed database and server status
- ✅ **Log viewing** - Text View node for easy log review and monitoring
- ✅ **Test running** - Validate everything works
- ✅ **Background operation** - Server runs in background
- ✅ **Clean UI** - Proper titles, subtitles, and icons
- ✅ **Pending file management** - Auto-reconcile or interactive browsing with refined UI
- ✅ **Compact notifications** - Clean, readable Alfred notifications
- ✅ **Enhanced reconciliation UI** - Improved visual presentation for better workflow

## 🔄 Updates

To update the workflow:
1. Replace the scripts in the `scripts/` directory
2. Update the `quillsidian_commands.json` if adding new commands
3. Update the `Quillsidian Server Manager.alfredworkflow` if needed
4. Re-import into Alfred

**Note**: The workflow file itself is maintained separately and should be exported/imported through Alfred's workflow management interface.

## 🎯 Workflow Structure

The workflow uses a modern Alfred structure with dual keyword support:

### Input Layer
- **Script Filter (quillsidian)** → Primary keyword with autocomplete
- **Script Filter (qs)** → Short alias keyword with same functionality

### Processing Layer
- **Conditional** → Routes commands to appropriate actions
  - Routes `list` command to Grid View for interactive browsing
  - Routes all other commands to server management script

### Action Layer
- **Run Script** → Executes `quillsidian.sh` for server management
- **Grid View** → Interactive pending file browser (for `list` command)
- **Run Script (Grid)** → Executes `reconcile.sh` for file reconciliation
- **Text View** → Displays logs for review (for `logs` command)

### Output Layer
- **Post Notification** → Shows compact results
- **Play Sound** → Audio feedback (Pop sound)

## 🚀 Recent Improvements

### Script Consolidation
- **Before**: 15+ individual scripts
- **After**: 4 focused scripts (`quillsidian.sh`, `reconcile.sh`, `script_filter.sh`, `pending_list.sh`)
- **Benefits**: Easier maintenance, fewer files, better organization, Alfred-compatible structure

### Enhanced Reconciliation
- **Auto-reconcile**: `quillsidian reconcile` processes all pending files
- **Interactive browser**: `quillsidian list` for selective reconciliation
- **Compact notifications**: Clean, readable results in Alfred
- **Alfred-compatible**: Dedicated `pending_list.sh` for Grid View compatibility

### Better Error Handling
- Server status checks before operations
- Graceful error messages
- Health monitoring integration

### Improved User Experience
- Consistent command structure
- Better autocomplete
- Cleaner notifications
- More intuitive workflow
- Text View node for log review
- Refined UI for reconciliation workflow

### Version 3.1.0 Updates
- Added Text View node for reviewing server logs
- Refined UI for reconciliation workflow
- Improved visual presentation in Grid View
- Enhanced user experience for log monitoring

---

**Version**: 3.1.0