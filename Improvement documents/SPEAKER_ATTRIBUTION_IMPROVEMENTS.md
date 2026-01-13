# Speaker Attribution Improvements

## Problem Description

The Quill server was experiencing speaker attribution issues where the same person (e.g., Christa Lyons) was being assigned multiple speaker IDs by Quill, resulting in inconsistent speaker labels in the generated transcripts. For example, in a 1:1 meeting with Christa, the transcript showed:

- **Chris McNeill** (correctly attributed)
- **Other Speakers** (should be Christa Lyons)
- **Speaker 1** (should also be Christa Lyons)

## Root Cause

Quill's diarization system sometimes creates separate speaker IDs for the same person, especially when:
- Audio quality varies during the meeting
- The person's voice characteristics change (e.g., due to background noise, distance from mic)
- Network connectivity issues cause audio dropouts
- The person moves or changes position during the call

## Solution Implemented

### 1. Speaker Similarity Detection

Added a `_compute_speaker_similarity()` function that analyzes multiple factors to determine if two speaker IDs likely represent the same person:

- **Text Similarity**: Uses difflib.SequenceMatcher to compare speech patterns
- **Source Similarity**: Checks if speakers use similar audio sources (remote, mic, etc.)
- **Timing Patterns**: Analyzes how speakers alternate and appear in sequence

### 2. Speaker Consolidation

Added a `_consolidate_similar_speakers()` function that:
- Groups transcript blocks by speaker ID
- Compares all speaker pairs for similarity
- Consolidates similar speakers by merging their speaker IDs
- Uses a configurable similarity threshold (default: 0.4)

### 3. Context-Aware Enhancement

Added `_enhance_speaker_attribution_with_context()` function that:
- Detects 1:1 meetings from titles
- Applies more aggressive consolidation for 1:1 meetings
- Uses meeting context to improve attribution accuracy

### 4. Configuration Options

Added new configuration options in `config.py`:
```python
enable_speaker_consolidation: bool = True
speaker_similarity_threshold: float = 0.4
```

### 5. Debug Endpoints

Added new debug endpoints for troubleshooting:
- `/debug/speaker_consolidation` - Shows speaker distribution before/after consolidation
- Enhanced `/debug/diarization_map` - Includes consolidation information

## Integration Points

The speaker consolidation is integrated into the main transcript rendering pipeline in `_render_transcript_body()`:

```python
# 1.5) NEW: Enhance speaker attribution with context-aware consolidation
if config.enable_speaker_consolidation:
    blocks = _enhance_speaker_attribution_with_context(
        blocks, meeting_title, quill_title, pref_names
    )
```

## Testing

Created test scripts to verify the functionality:
- `test_speaker_consolidation_simple.py` - Standalone test without Flask dependencies
- Test data simulates the Christa meeting scenario with multiple speaker IDs

## Expected Results

For the Christa meeting issue:
- **Before**: 3 speaker IDs (speaker_1, speaker_2, speaker_3)
- **After**: 2 speaker IDs (speaker_1, speaker_2) - speaker_3 consolidated into speaker_2
- **Result**: Consistent "Christa Lyons" attribution throughout the transcript

## Configuration

The feature can be controlled via configuration:

```python
# Disable speaker consolidation
config.enable_speaker_consolidation = False

# Adjust similarity threshold (0.0 = very aggressive, 1.0 = very conservative)
config.speaker_similarity_threshold = 0.4
```

## Monitoring

Use the debug endpoints to monitor speaker consolidation:
```bash
# Check speaker consolidation for a specific meeting
curl "http://localhost:5001/debug/speaker_consolidation?meeting_id=YOUR_MEETING_ID"

# Check general diarization mapping
curl "http://localhost:5001/debug/diarization_map?meeting_id=YOUR_MEETING_ID"
```

## Future Enhancements

Potential improvements for future versions:
1. **Voice Pattern Analysis**: Use more sophisticated audio analysis
2. **Machine Learning**: Train models on known speaker patterns
3. **Meeting History**: Use historical data to improve attribution
4. **Real-time Feedback**: Allow manual correction with learning
5. **Confidence Scoring**: Provide confidence levels for attributions

## Troubleshooting

If speaker consolidation is too aggressive or not aggressive enough:

1. **Too Aggressive**: Increase `speaker_similarity_threshold` (e.g., 0.5, 0.6)
2. **Not Aggressive Enough**: Decrease `speaker_similarity_threshold` (e.g., 0.3, 0.2)
3. **Disable for Testing**: Set `enable_speaker_consolidation = False`

Use the debug endpoints to analyze the similarity scores and adjust accordingly.
