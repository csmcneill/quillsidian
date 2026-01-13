# Filename Sanitization Improvements

## Overview

Enhanced the filename sanitization functions in both Quillsidian and Linear sync scripts to handle problematic characters that macOS doesn't like, replacing them with visually similar but filesystem-safe alternatives.

## Problem

macOS has issues with certain characters in filenames, particularly:
- `:` (colon) - causes issues with file paths and URLs
- `/` (forward slash) - directory separator
- `\` (backslash) - escape character
- `*` (asterisk) - wildcard character
- `?` (question mark) - wildcard character
- `"` (double quotes) - string delimiter
- `'` (smart quotes) - special Unicode characters
- `<` and `>` (angle brackets) - HTML/XML characters
- `|` (pipe) - command separator

## Solution

Updated the `sanitize_filename` function in both:
- `1. Projects/Work/Quillsidian/validation.py`
- `3. Resources/Linear/sync_linear_with_openai.py`

### Character Replacements

| Original | Replacement | Description |
|----------|-------------|-------------|
| `:` | `ː` | Colon → modifier letter colon (visually similar) |
| `–` | `–` | En dash → en dash (keep consistent) |
| `—` | `–` | Em dash → en dash |
| `/` | `-` | Forward slash → hyphen |
| `\` | `-` | Backslash → hyphen |
| `*` | `∗` | Asterisk → asterisk operator |
| `?` | `？` | Question mark → fullwidth question mark |
| `"` | `'` | Double quotes → single quotes |
| `'` | `'` | Smart single quotes → regular single quotes |
| `<` | `(` | Less-than sign → left parenthesis |
| `>` | `)` | Greater-than sign → right parenthesis |
| `|` | `-` | Vertical line → hyphen |

## Key Improvements

1. **Consistent handling**: Both Quillsidian and Linear now use the same sanitization logic
2. **Visual similarity**: Replacements maintain visual similarity to original characters
3. **Filesystem safety**: All replacements are safe for macOS filesystem
4. **Backward compatibility**: Existing files with `ː` characters continue to work
5. **Comprehensive coverage**: Handles all common problematic characters

## Testing

Created and ran `test_filename_sanitization.py` to verify all character replacements work correctly:

```bash
python3 test_filename_sanitization.py
```

All tests pass, confirming the sanitization function works as expected.

## Examples

### Before (problematic)
- `1:1 with Doug Aitken`
- `Meeting: Important Discussion`
- `File/with/slashes`
- `File"with"quotes`
- `File<with>brackets`

### After (filesystem-safe)
- `1ː1 with Doug Aitken`
- `Meetingː Important Discussion`
- `File-with-slashes`
- `File'with'quotes`
- `File(with)brackets`

## Impact

- **Quillsidian**: All new transcript and summary files will have macOS-compatible filenames
- **Linear**: All new Linear issue files will have macOS-compatible filenames
- **Consistency**: Both systems now handle problematic characters consistently
- **User experience**: No more filesystem errors or display issues with special characters
