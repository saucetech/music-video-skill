#!/usr/bin/env bash
# Transcribe audio using OpenAI Whisper with word-level timestamps.
# Uses large-v3 for music-grade accuracy, with automatic fallback.
#
# Usage: transcribe.sh <audio-file> <output-dir> [--vocals-stem <path>]
#
# If --vocals-stem is provided, transcription runs on the isolated vocals
# (from demucs) for better accuracy. Otherwise uses the original audio.

set -euo pipefail

AUDIO_FILE="${1:?Usage: transcribe.sh <audio-file> <output-dir> [--vocals-stem <path>]}"
OUTPUT_DIR="${2:?Usage: transcribe.sh <audio-file> <output-dir> [--vocals-stem <path>]}"
VOCALS_STEM=""

# Parse optional flags
shift 2
while [[ $# -gt 0 ]]; do
  case $1 in
    --vocals-stem) VOCALS_STEM="$2"; shift 2 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

# Validate input
if [[ ! -f "$AUDIO_FILE" ]]; then
  echo "ERROR: Audio file not found: $AUDIO_FILE" >&2
  exit 1
fi

TRANSCRIBE_SOURCE="$AUDIO_FILE"
if [[ -n "$VOCALS_STEM" && -f "$VOCALS_STEM" ]]; then
  TRANSCRIBE_SOURCE="$VOCALS_STEM"
  echo "Using isolated vocals for transcription: $VOCALS_STEM"
else
  echo "Using original audio for transcription: $AUDIO_FILE"
fi

mkdir -p "$OUTPUT_DIR"

# Check for Whisper
if ! python3 -c "import whisper" 2>/dev/null; then
  echo "Whisper not found. Installing openai-whisper..." >&2
  pip install openai-whisper
fi

# Get audio duration
DURATION=$(ffprobe -v quiet -show_entries format=duration -of csv=p=0 "$AUDIO_FILE" 2>/dev/null || echo "unknown")
echo "Audio duration: ${DURATION}s"

# Try models in order: large-v3 → medium → base
export TRANSCRIBE_SOURCE OUTPUT_DIR
python3 << 'PYEOF'
import whisper
import json
import sys
import os

audio_path = os.environ.get("TRANSCRIBE_SOURCE", sys.argv[1] if len(sys.argv) > 1 else "")
output_dir = os.environ.get("OUTPUT_DIR", sys.argv[2] if len(sys.argv) > 2 else "")

models_to_try = ["large-v3", "medium", "base"]
result = None
model_used = None

for model_name in models_to_try:
    try:
        print(f"Loading Whisper model: {model_name}...", file=sys.stderr)
        model = whisper.load_model(model_name)
        print(f"Transcribing with {model_name}...", file=sys.stderr)
        result = model.transcribe(audio_path, word_timestamps=True, language=None)
        model_used = model_name
        print(f"Transcription complete with {model_name}", file=sys.stderr)
        break
    except Exception as e:
        print(f"Model {model_name} failed: {e}", file=sys.stderr)
        if model_name == models_to_try[-1]:
            print("ERROR: All Whisper models failed", file=sys.stderr)
            sys.exit(1)
        print(f"Falling back to next model...", file=sys.stderr)
        continue

output = {
    "text": result["text"],
    "language": result.get("language", "en"),
    "model_used": model_used,
    "segments": [],
}

for segment in result["segments"]:
    seg = {
        "id": segment["id"],
        "start": segment["start"],
        "end": segment["end"],
        "text": segment["text"].strip(),
        "words": [],
    }
    for word_info in segment.get("words", []):
        seg["words"].append({
            "word": word_info["word"].strip(),
            "start": round(word_info["start"], 3),
            "end": round(word_info["end"], 3),
            "probability": round(word_info.get("probability", 0), 3),
        })
    output["segments"].append(seg)

output_path = os.path.join(output_dir, "whisper-raw.json")
with open(output_path, "w") as f:
    json.dump(output, f, indent=2)

total_words = sum(len(s["words"]) for s in output["segments"])
print(f"Transcription complete: {len(output['segments'])} segments, {total_words} words (model: {model_used})")
PYEOF

echo "Output saved to: ${OUTPUT_DIR}/whisper-raw.json"
