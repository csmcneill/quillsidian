# Quillsidian

A Python Flask server that processes Quill webhooks to automatically generate Markdown summaries and transcripts for Obsidian. Quillsidian bridges the gap between Quill's meeting recordings and your Obsidian knowledge base.

## Features

- **Automatic Processing**: Receives webhooks from Quill and saves meeting summaries
- **Smart Matching**: Automatically matches summaries with transcripts using participant overlap, title similarity, time proximity, and transcript snippets
- **Speaker Attribution**: Intelligently identifies and labels speakers in transcripts
- **Obsidian Integration**: Generates properly formatted Markdown files with YAML frontmatter
- **Alfred Workflow**: Optional Alfred workflow for easy server management
- **Flexible Configuration**: Supports both config files and environment variables

## Prerequisites

- Python 3.8 or higher
- Flask (installed automatically)
- Quill macOS app with webhook support
- Obsidian (or any Markdown-compatible note-taking app)
- Access to Quill's SQLite database (`~/Library/Application Support/Quill/quill.db`)

## Installation

1. **Clone or download this repository**

2. **Create a virtual environment**:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate  # On macOS/Linux
   # or
   .venv\Scripts\activate  # On Windows
   ```

3. **Install dependencies**:
   ```bash
   pip install flask
   ```

4. **Configure the server**:
   ```bash
   cp config.example.py config.py
   # Edit config.py with your specific paths and settings
   ```

## Configuration

### Quick Start

1. Copy `config.example.py` to `config.py`
2. Edit `config.py` with your settings:
   - `notes_root`: Path to your Quillsidian project directory
   - `quill_db_path`: Path to Quill's database (default: `~/Library/Application Support/Quill/quill.db`)
   - `canonical_name`: Your full name (used for speaker attribution)
   - `aliases`: Set of aliases for your name (e.g., `{"me", "your-first-name"}`)
   - `summaries_root` and `transcripts_root`: Where to save generated files

### Environment Variables

You can override any config setting using environment variables:

- `QUILL_NOTES_ROOT`: Path to Quillsidian project
- `QUILL_DB_PATH`: Path to Quill database
- `QUILL_CANONICAL_NAME`: Your full name
- `QUILL_ALIASES`: Comma-separated aliases (e.g., `"me,chris"`)
- `QUILL_SUMMARIES_ROOT`: Path for summaries (overrides default)
- `QUILL_TRANSCRIPTS_ROOT`: Path for transcripts (overrides default)
- `QUILL_PORT`: Server port (default: 5001)
- `QUILL_HOST`: Server host (default: 0.0.0.0)
- `QUILL_AUTO_RECONCILE`: Auto-reconcile on summary (default: "true")
- `QUILL_WINDOW_HOURS`: Time window for matching (default: 36)

### Path Setup

By default, Quillsidian expects this directory structure:
```
Your Notes/
├── 1. Projects/
│   └── Work/
│       └── Quillsidian/     # notes_root
│           ├── config.py
│           ├── quill_server.py
│           └── ...
└── 3. Resources/
    ├── Summaries/            # summaries_root
    └── Transcripts/          # transcripts_root
```

You can customize these paths in `config.py` or via environment variables.

## Usage

### Starting the Server

```bash
# Activate virtual environment
source .venv/bin/activate

# Start the server
python3 quill_server.py
```

The server will start on `http://localhost:5001` by default.

### Setting Up Quill Webhooks

1. Open Quill app
2. Go to Settings → Webhooks
3. Add a new webhook:
   - URL: `http://localhost:5001/quill_summary`
   - Method: POST
   - Content-Type: application/json

### API Endpoints

- `POST /quill_summary` - Receive summary webhooks from Quill
- `POST /reconcile/auto` - Auto-reconcile all pending files
- `POST /reconcile/pick` - Manually reconcile a specific pending file
- `GET /health` - Health check endpoint
- `GET /config` - View current configuration
- `GET /pending/list` - List all pending files
- `GET /pending/candidates` - Get candidate meetings for a pending file

### Alfred Workflow (Optional)

See `alfred-workflow/README.md` for instructions on using the Alfred workflow for server management.

## How It Works

1. **Webhook Reception**: Quill sends a summary webhook to `/quill_summary`
2. **Summary Saving**: The server saves the summary as a Markdown file
3. **Pending File Creation**: A `.pending.json` file is created with meeting metadata
4. **Auto-Reconciliation** (if enabled): The server searches Quill's database for matching transcripts
5. **Transcript Generation**: If a match is found, the transcript is rendered and saved
6. **Cross-Linking**: Summary and transcript files are linked via Obsidian wikilinks

### Matching Algorithm

Quillsidian uses a sophisticated matching algorithm that considers:

- **Participant Overlap**: How many participants match between summary and transcript
- **Title Similarity**: How similar the meeting titles are
- **Time Proximity**: How close the meeting times are
- **Transcript Snippets**: Direct text matching from transcript excerpts

Different session types (1-on-1, internal-sync, external-sync, etc.) have different confidence thresholds and weight distributions.

## Enhanced Quill Templates

The `Enhanced Quill Templates/` directory contains example prompts for different meeting types. These templates help Quill generate properly formatted JSON that Quillsidian can process.

See `Enhanced Quill Templates/README.md` for:
- How Quill structures meeting data
- How to customize templates for your use case
- Best practices for prompt engineering

**Note**: These templates are examples based on the author's specific workflow. You may need to customize them for your needs.

## Troubleshooting

### Server Won't Start

- Check if port 5001 is available: `lsof -i :5001`
- Verify Flask is installed: `pip list | grep flask`
- Check logs for errors

### Summaries Not Matching Transcripts

- Verify `quill_db_path` points to the correct database
- Check that `canonical_name` and `aliases` are configured correctly
- Review server logs for matching scores
- Try manual reconciliation via `/reconcile/pick`

### Speaker Attribution Issues

- Configure your `canonical_name` and `aliases` in `config.py`
- Check `local_sources` configuration
- Use manual overrides in `.quill_overrides/` directory
- Review speaker consolidation settings

### Path Issues

- Ensure all paths in `config.py` are absolute or relative to the project root
- Check that directories exist or will be created automatically
- Verify write permissions for output directories

## Project Structure

```
Quillsidian/
├── quill_server.py          # Main Flask server
├── config.py                # Your configuration (gitignored)
├── config.example.py        # Configuration template
├── database.py              # Database operations
├── validation.py            # Webhook payload validation
├── logging_config.py        # Logging configuration
├── .venv/                   # Python virtual environment (gitignored)
├── .quill_overrides/        # Manual speaker overrides (gitignored)
├── Enhanced Quill Templates/ # Example Quill prompts
├── alfred-workflow/         # Optional Alfred workflow
└── README.md                # This file
```

## Development

### Running Tests

```bash
source .venv/bin/activate
python3 test_improvements.py  # If test files exist
```

### Logging

Server logs are written to:
- Console output (when running directly)
- `server.log` (if configured)
- `/tmp/quill_server.log` (when run via Alfred workflow)

## Contributing

Contributions are welcome! Please feel free to submit issues or pull requests.

## License

See `LICENSE` file for details.

## Acknowledgments

- Built for use with [Quill](https://quill.ai/) meeting recording app
- Designed to work with [Obsidian](https://obsidian.md/) knowledge base

## Support

For issues, questions, or contributions, please open an issue on GitHub.
