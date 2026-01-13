# Enhanced Quill Templates Guide

This directory contains example prompts for different meeting types that work with Quill and Quillsidian. These templates are **examples** based on the author's specific use case and may not work perfectly for every situation. This guide explains how Quill structures meeting data and how to customize these templates for your needs.

## Understanding Quill's Data Structure

### How Quill Stores Meeting Data

Quill stores meeting information in a SQLite database (`quill.db`) located at:
- macOS: `~/Library/Application Support/Quill/quill.db`

Key tables and fields:
- **Meeting table**: Contains meeting metadata
  - `id`: Unique meeting identifier
  - `title`: Meeting title as shown in Quill UI
  - `participants`: Comma-separated string of participant names
  - `start`: Start timestamp (milliseconds since epoch)
  - `end`: End timestamp (milliseconds since epoch)
  - `audio_transcript`: JSON blob containing transcript blocks

- **ContactMeeting table**: Contains speaker mappings (more accurate than `participants` field)
  - Maps `speaker_id` to actual names
  - More reliable than parsing `participants` string

### Transcript Structure

The `audio_transcript` field contains a JSON structure with blocks:
```json
{
  "blocks": [
    {
      "speaker_id": "0",
      "text": "Hello, how are you?",
      "start": 1234567890,
      "end": 1234567895,
      "source": "mic"
    },
    ...
  ]
}
```

### Key Learnings

1. **Participant Lists Are Unreliable**
   - The `participants` field often contains email-based names (e.g., "jn@stripe.com" → "Jn")
   - Calendar invitees may not match actual attendees
   - Undefined speakers appear as "Speaker 1", "Speaker 2"
   - Uninvited participants may join calls
   - Conference room attendees may be included

2. **Transcript Matching is Most Reliable**
   - Matching based on transcript snippets is more accurate than participant lists
   - Always include a `transcript_snippet` field in your prompts
   - Use the first 500-1000 characters of the transcript for matching

3. **Speaker Attribution Challenges**
   - Quill may assign multiple speaker IDs to the same person
   - Local speaker (you) is typically identified by `source: "mic"` or `source: "local"`
   - Speaker names from ContactMeeting table are more accurate than inferred names

4. **Meeting Titles Vary**
   - Quill's `Meeting.title` may not match calendar event titles
   - Titles often include dates, participants, or other metadata
   - Use `quill_title` field to store the exact Quill title for matching

5. **Date Handling**
   - Quill stores timestamps in milliseconds since epoch (UTC)
   - Meeting dates should be extracted from actual conversation, not calendar scheduling
   - Use `YYYY-MM-DD` format consistently

## Customizing Templates

### Step 1: Replace Placeholders

All templates use `{{Your Name}}` as a placeholder. Replace this with your actual name:
- In the `participants` array
- In the YAML frontmatter examples
- In any participant-related instructions

### Step 2: Adjust Session Types

The templates include these session types:
- `1-on-1`: One-on-one meetings
- `internal-sync`: Internal team meetings
- `external-sync`: Meetings with external partners/vendors
- `note-to-self`: Personal reflections or solo work sessions
- `other`: Meetings that don't fit other categories

Add or modify session types based on your meeting patterns.

### Step 3: Customize Tags

Each template includes suggested tags. Modify these based on your organizational needs:
- Project-specific tags (e.g., `#stripe`, `#merchant-of-record`)
- Topic tags (e.g., `#pricing`, `#compliance`)
- Meeting type tags (e.g., `#1-on-1`, `#vendor-sync`)

### Step 4: Adjust Metadata Fields

Consider adding custom YAML frontmatter fields:
- `project`: Project or team name
- `external_partners`: For vendor/external meetings
- `next_meeting_focus`: Follow-up topics
- Custom fields specific to your workflow

### Step 5: Modify Summary Structure

Each template has a specific summary structure. Adjust sections based on what's useful for you:
- Key Topics Discussed
- Followup Tasks
- Open Questions
- Detailed Notes
- Optional sections (Personal Insight, Manager Commitments, etc.)

## Best Practices

### Prompt Engineering Tips

1. **Be Explicit About JSON Format**
   - Quill's AI needs clear instructions about JSON structure
   - Specify exact formatting requirements (indentation, escaping, etc.)
   - Include examples of valid JSON output

2. **Emphasize Transcript Snippets**
   - Always request a transcript snippet for matching
   - Specify length (500-1000 characters)
   - Include instructions to truncate with `<SNIP>` if needed

3. **Handle Edge Cases**
   - Instruct the AI to use `null` for unavailable fields
   - Specify fallback behavior for missing data
   - Handle date extraction from conversation vs. calendar

4. **Validate Output**
   - Request valid JSON (no markdown code blocks)
   - Specify escaping requirements (`\n`, `\"`)
   - Set maximum field lengths to prevent truncation

### Matching Accuracy

To improve matching accuracy between summaries and transcripts:

1. **Include Distinctive Transcript Snippets**
   - Use the very beginning of the conversation
   - Include unique phrases or topics discussed early
   - Avoid generic greetings if possible

2. **Use Consistent Naming**
   - Configure your canonical name in `config.py`
   - Set appropriate aliases (e.g., "me", your first name)
   - Ensure participant names match across summaries and transcripts

3. **Leverage Session Types**
   - Different session types have different matching thresholds
   - Configure confidence thresholds in `config.py`
   - Adjust weights for participant overlap vs. title similarity

## Template Files

- **ENHANCED_QUILL_PROMPT_1ON1.md**: One-on-one meetings
- **ENHANCED_QUILL_PROMPT_INTERNAL_SYNC.md**: Internal team meetings
- **ENHANCED_QUILL_PROMPT_VENDOR_SYNC.md**: External/vendor meetings
- **ENHANCED_QUILL_PROMPT_NOTE_TO_SELF.md**: Personal reflections
- **ENHANCED_QUILL_PROMPT_OTHER.md**: Other meeting types

## Using Templates in Quill

1. Copy a template that matches your meeting type
2. Replace `{{Your Name}}` with your actual name
3. Customize tags, metadata fields, and structure as needed
4. Use the template as a prompt in Quill's AI features
5. Quill will generate JSON output that Quillsidian can process

## Troubleshooting

### Matching Failures

If summaries aren't matching with transcripts:
- Check that `transcript_snippet` is included and distinctive
- Verify participant names match your configuration
- Review confidence thresholds in `config.py`
- Check server logs for matching scores

### Speaker Attribution Issues

If speakers aren't being identified correctly:
- Configure your `canonical_name` and `aliases` in `config.py`
- Check `local_sources` configuration
- Review speaker consolidation settings
- Use manual overrides in `.quill_overrides/` directory

### JSON Parsing Errors

If Quill's output isn't valid JSON:
- Review JSON formatting requirements in templates
- Check for unescaped quotes or newlines
- Verify indentation matches requirements
- Ensure no markdown code blocks wrap the JSON

## Further Reading

- See `config.py` for configuration options
- Check `quill_server.py` for matching algorithm details
- Review `README.md` for general Quillsidian setup

## Notes

These templates represent the author's specific use case and workflow. They may not be perfect for every situation, but they provide a solid foundation for customizing your own prompts. The key is understanding how Quill structures data and adjusting the templates to match your needs.
