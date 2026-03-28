#!/usr/bin/env bash
set -euo pipefail

# ─────────────────────────────────────────────────────────────────────────────
# assemble.sh — Final assembly for the music video pipeline
#
# Chains video clips with beat-synced crossfade transitions, overlays captions,
# applies color normalization, and muxes the original audio track.
#
# Usage:
#   bash scripts/assemble.sh \
#     --clips ./clips/ \
#     --audio song.mp3 \
#     --mode karaoke|music-video \
#     --captions ./karaoke.ass OR ./kinetic-frames/ \
#     --beat-map ./beat-map.json \
#     --song-structure ./song-structure.json \
#     --output ./final.mp4 \
#     [--beat-effects]
#     [--transition-duration 1.0]
# ─────────────────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMP_DIR=".assembly-tmp"

# ── Defaults ────────────────────────────────────────────────────────────────

CLIPS_DIR=""
AUDIO=""
MODE=""
CAPTIONS=""
BEAT_MAP=""
SONG_STRUCTURE=""
OUTPUT=""
BEAT_EFFECTS=false
TRANSITION_DURATION=""

# ── Argument parsing ────────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
  case "$1" in
    --clips)
      CLIPS_DIR="$2"; shift 2 ;;
    --audio)
      AUDIO="$2"; shift 2 ;;
    --mode)
      MODE="$2"; shift 2 ;;
    --captions)
      CAPTIONS="$2"; shift 2 ;;
    --beat-map)
      BEAT_MAP="$2"; shift 2 ;;
    --song-structure)
      SONG_STRUCTURE="$2"; shift 2 ;;
    --output)
      OUTPUT="$2"; shift 2 ;;
    --beat-effects)
      BEAT_EFFECTS=true; shift ;;
    --transition-duration)
      TRANSITION_DURATION="$2"; shift 2 ;;
    *)
      echo "ERROR: Unknown argument: $1" >&2
      exit 1 ;;
  esac
done

# ── Validation ──────────────────────────────────────────────────────────────

missing=()
[[ -z "$CLIPS_DIR" ]]      && missing+=("--clips")
[[ -z "$AUDIO" ]]          && missing+=("--audio")
[[ -z "$MODE" ]]           && missing+=("--mode")
[[ -z "$CAPTIONS" ]]       && missing+=("--captions")
[[ -z "$BEAT_MAP" ]]       && missing+=("--beat-map")
[[ -z "$SONG_STRUCTURE" ]] && missing+=("--song-structure")
[[ -z "$OUTPUT" ]]         && missing+=("--output")

if [[ ${#missing[@]} -gt 0 ]]; then
  echo "ERROR: Missing required arguments: ${missing[*]}" >&2
  exit 1
fi

if [[ "$MODE" != "karaoke" && "$MODE" != "music-video" ]]; then
  echo "ERROR: --mode must be 'karaoke' or 'music-video', got '$MODE'" >&2
  exit 1
fi

if [[ ! -d "$CLIPS_DIR" ]]; then
  echo "ERROR: Clips directory not found: $CLIPS_DIR" >&2
  exit 1
fi

if [[ ! -f "$AUDIO" ]]; then
  echo "ERROR: Audio file not found: $AUDIO" >&2
  exit 1
fi

if [[ ! -f "$BEAT_MAP" ]]; then
  echo "ERROR: Beat map not found: $BEAT_MAP" >&2
  exit 1
fi

if [[ ! -f "$SONG_STRUCTURE" ]]; then
  echo "ERROR: Song structure not found: $SONG_STRUCTURE" >&2
  exit 1
fi

if [[ "$MODE" == "karaoke" && ! -f "$CAPTIONS" ]]; then
  echo "ERROR: Captions .ass file not found: $CAPTIONS" >&2
  exit 1
fi

if [[ "$MODE" == "music-video" && ! -d "$CAPTIONS" ]]; then
  echo "ERROR: Kinetic frames directory not found: $CAPTIONS" >&2
  exit 1
fi

# Check dependencies
for cmd in ffmpeg ffprobe python3; do
  if ! command -v "$cmd" &>/dev/null; then
    echo "ERROR: Required command not found: $cmd" >&2
    exit 1
  fi
done

# ── Setup ───────────────────────────────────────────────────────────────────

rm -rf "$TMP_DIR"
mkdir -p "$TMP_DIR"

cleanup() {
  echo ""
  echo "[cleanup] Removing temp directory: $TMP_DIR"
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

step=0
log_step() {
  step=$((step + 1))
  echo ""
  echo "═══════════════════════════════════════════════════════════════"
  echo "  Step $step: $1"
  echo "═══════════════════════════════════════════════════════════════"
  echo ""
}

# ── Step 1: Normalize all clips ────────────────────────────────────────────

log_step "Normalizing clips to 1920x1080 @ 30fps, yuv420p + color normalization"

CLIP_FILES=()
while IFS= read -r -d '' f; do
  CLIP_FILES+=("$f")
done < <(find "$CLIPS_DIR" -maxdepth 1 -type f \( -name "*.mp4" -o -name "*.mov" -o -name "*.mkv" -o -name "*.avi" -o -name "*.webm" \) -print0 | sort -z)

if [[ ${#CLIP_FILES[@]} -eq 0 ]]; then
  echo "ERROR: No video files found in $CLIPS_DIR" >&2
  exit 1
fi

echo "  Found ${#CLIP_FILES[@]} clip(s)"

NORMALIZED_CLIPS=()
for i in "${!CLIP_FILES[@]}"; do
  src="${CLIP_FILES[$i]}"
  dst="$TMP_DIR/norm_$(printf '%03d' "$i").mp4"
  echo "  [$((i+1))/${#CLIP_FILES[@]}] $(basename "$src") → $(basename "$dst")"

  ffmpeg -y -i "$src" \
    -vf "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,normalize=blackpt=black:whitept=white:smoothing=20" \
    -c:v libx264 -preset fast -crf 18 \
    -pix_fmt yuv420p \
    -an \
    -movflags +faststart \
    "$dst" 2>/dev/null

  NORMALIZED_CLIPS+=("$dst")
done

echo "  Normalized ${#NORMALIZED_CLIPS[@]} clips"

# ── Step 2: Chain clips with beat-synced crossfade transitions ─────────────

log_step "Building beat-synced crossfade transition chain"

CHAINED_VIDEO="$TMP_DIR/chained.mp4"

if [[ ${#NORMALIZED_CLIPS[@]} -eq 1 ]]; then
  echo "  Only one clip — skipping transitions"
  cp "${NORMALIZED_CLIPS[0]}" "$CHAINED_VIDEO"
else
  # Use python3 to parse JSON and build the xfade filtergraph
  XFADE_SCRIPT="$TMP_DIR/build_xfade.py"

  cat > "$XFADE_SCRIPT" << 'PYEOF'
import json
import sys
import subprocess
import os

clips_json = sys.argv[1]        # JSON array of clip paths
beat_map_path = sys.argv[2]     # beat-map.json
song_struct_path = sys.argv[3]  # song-structure.json
default_td = sys.argv[4]        # default transition duration (or empty)
output_path = sys.argv[5]       # output video path
tmp_dir = sys.argv[6]           # temp directory

clips = json.loads(clips_json)

with open(beat_map_path) as f:
    beat_map = json.load(f)

with open(song_struct_path) as f:
    song_structure = json.load(f)

# Extract beat timestamps — support both flat array and {beats: [...]} formats
if isinstance(beat_map, list):
    beats = [float(b) for b in beat_map]
elif isinstance(beat_map, dict):
    beats = [float(b) for b in beat_map.get("beats", beat_map.get("beat_times", []))]
else:
    beats = []

if not beats:
    print("WARNING: No beats found in beat-map.json, using raw cut points", file=sys.stderr)

# Build section boundaries from song structure
# Expected format: [{start: float, end: float, label: "verse"}, ...]
sections = []
if isinstance(song_structure, list):
    sections = song_structure
elif isinstance(song_structure, dict):
    sections = song_structure.get("sections", song_structure.get("structure", []))

def get_section_at(t):
    """Return section label at time t."""
    for s in sections:
        start = float(s.get("start", 0))
        end = float(s.get("end", float("inf")))
        if start <= t < end:
            return s.get("label", s.get("type", "unknown")).lower()
    return "unknown"

def snap_to_beat(t):
    """Snap a timestamp to the nearest beat."""
    if not beats:
        return t
    closest = min(beats, key=lambda b: abs(b - t))
    return closest

def get_transition(section_from, section_to):
    """Determine transition type and duration based on section boundary."""
    key = (section_from, section_to)

    # Specific section transition rules
    if section_from == "verse" and section_to == "verse":
        return ("dissolve", 1.0)
    elif section_from == "verse" and section_to == "chorus":
        return ("none", 0.0)  # hard cut
    elif section_from == "chorus" and section_to == "chorus":
        return ("none", 0.0)  # hard cut
    elif section_from == "chorus" and section_to == "verse":
        return ("fadeblack", 0.8)
    elif section_to == "bridge":
        return ("dissolve", 1.2)
    elif section_from == "bridge":
        return ("dissolve", 1.0)
    else:
        return ("dissolve", 0.8)

# Get durations of each clip
durations = []
for clip in clips:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", clip],
        capture_output=True, text=True
    )
    dur = float(result.stdout.strip())
    durations.append(dur)

# Determine cut points and transition types
# Each cut point is at the boundary between clip[i] and clip[i+1]
cumulative = 0.0
transitions = []  # list of (xfade_type, duration)

for i in range(len(clips) - 1):
    raw_cut = cumulative + durations[i]
    snapped_cut = snap_to_beat(raw_cut)

    section_before = get_section_at(snapped_cut - 0.1)
    section_after = get_section_at(snapped_cut + 0.1)

    xfade_type, td = get_transition(section_before, section_after)

    # Override with user-specified transition duration if provided
    if default_td:
        td = float(default_td)

    transitions.append((xfade_type, td))
    cumulative = raw_cut

# Build ffmpeg command with xfade filter chain
# For N clips, we need N-1 xfade filters chained together
# offset_n = sum(durations[0..n]) - sum(transition_durations[0..n-1]) - transition_durations[n]

inputs = []
for clip in clips:
    inputs.extend(["-i", clip])

# Build the filter graph
filter_parts = []
cumulative_dur = durations[0]

for i in range(len(clips) - 1):
    xfade_type, td = transitions[i]

    if xfade_type == "none" or td <= 0:
        # Hard cut: use concat instead of xfade for this pair
        # We handle this by using xfade with duration=0 which is effectively a cut
        # Actually ffmpeg xfade needs duration > 0, so we use a very small dissolve
        td = 0.0

    # Offset = cumulative duration so far minus the transition duration
    offset = cumulative_dur - td
    if offset < 0:
        offset = 0

    if i == 0:
        src = "[0:v]"
    else:
        src = f"[xf{i-1}]"

    dst_label = f"[xf{i}]"

    if td <= 0:
        # Hard cut — use ultra-short xfade instead of concat for compatibility
        td = 0.033  # ~1 frame at 30fps
        filter_parts.append(
            f"{src}[{i+1}:v]xfade=transition=fade:duration={td:.3f}:offset={offset:.3f}{dst_label}"
        )
    else:
        filter_parts.append(
            f"{src}[{i+1}:v]xfade=transition={xfade_type}:duration={td:.3f}:offset={offset:.3f}{dst_label}"
        )

    # Update cumulative duration: add next clip duration minus overlap
    cumulative_dur = cumulative_dur + durations[i + 1] - td

# The last label is our final output
if len(clips) > 1:
    last_label = f"xf{len(clips) - 2}"
else:
    last_label = "0:v"

filter_complex = ";".join(filter_parts)

cmd = ["ffmpeg", "-y"] + inputs + [
    "-filter_complex", filter_complex,
    "-map", f"[{last_label}]",
    "-c:v", "libx264", "-preset", "fast", "-crf", "18",
    "-pix_fmt", "yuv420p",
    "-an",
    output_path
]

print(f"  Running xfade chain with {len(transitions)} transition(s)...")
for i, (t, d) in enumerate(transitions):
    label = "hard cut" if t == "none" or d <= 0 else f"{t} ({d:.1f}s)"
    print(f"    Transition {i+1}: {label}")

result = subprocess.run(cmd, capture_output=True, text=True)
if result.returncode != 0:
    print(f"ERROR: ffmpeg xfade failed:\n{result.stderr}", file=sys.stderr)
    sys.exit(1)

print(f"  Chained video written to {output_path}")
PYEOF

  # Build JSON array of clip paths for the xfade script
  CLIPS_JSON=$(python3 -c "
import json, sys
clips = sys.argv[1:]
print(json.dumps(clips))
" "${NORMALIZED_CLIPS[@]}")

  python3 "$XFADE_SCRIPT" \
    "$CLIPS_JSON" \
    "$BEAT_MAP" \
    "$SONG_STRUCTURE" \
    "$TRANSITION_DURATION" \
    "$CHAINED_VIDEO" \
    "$TMP_DIR"
fi

if [[ ! -f "$CHAINED_VIDEO" ]]; then
  echo "ERROR: Chained video was not created" >&2
  exit 1
fi

echo "  Chained video: $(du -h "$CHAINED_VIDEO" | cut -f1)"

# ── Step 3: Beat-reactive effects (optional) ───────────────────────────────

EFFECTS_VIDEO="$TMP_DIR/effects.mp4"

if [[ "$BEAT_EFFECTS" == true ]]; then
  log_step "Applying beat-reactive effects (zoom pulse, brightness flash, saturation boost)"

  EFFECTS_SCRIPT="$TMP_DIR/build_effects.py"

  cat > "$EFFECTS_SCRIPT" << 'PYEOF'
import json
import sys
import subprocess
import math

chained_video = sys.argv[1]
beat_map_path = sys.argv[2]
song_struct_path = sys.argv[3]
output_path = sys.argv[4]
tmp_dir = sys.argv[5]

with open(beat_map_path) as f:
    beat_map = json.load(f)

with open(song_struct_path) as f:
    song_structure = json.load(f)

# Extract beats and onsets
if isinstance(beat_map, list):
    beats = [float(b) for b in beat_map]
    onsets = beats  # fallback: use beats as onsets
elif isinstance(beat_map, dict):
    beats = [float(b) for b in beat_map.get("beats", beat_map.get("beat_times", []))]
    onsets = [float(o) for o in beat_map.get("onsets", beat_map.get("onset_times", beats))]
else:
    beats = []
    onsets = []

# Extract sections for chorus detection
sections = []
if isinstance(song_structure, list):
    sections = song_structure
elif isinstance(song_structure, dict):
    sections = song_structure.get("sections", song_structure.get("structure", []))

chorus_ranges = []
for s in sections:
    label = s.get("label", s.get("type", "")).lower()
    if label == "chorus":
        chorus_ranges.append((float(s.get("start", 0)), float(s.get("end", 0))))

def is_chorus(t):
    for start, end in chorus_ranges:
        if start <= t <= end:
            return True
    return False

# Get video duration
result = subprocess.run(
    ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
     "-of", "csv=p=0", chained_video],
    capture_output=True, text=True
)
total_dur = float(result.stdout.strip())

# If more than 200 beats, process in 30-second chunks
CHUNK_SIZE = 30.0
num_chunks = max(1, math.ceil(total_dur / CHUNK_SIZE)) if len(beats) > 200 else 1
use_chunks = len(beats) > 200

if use_chunks:
    print(f"  {len(beats)} beats detected — processing in {num_chunks} chunks of {CHUNK_SIZE}s")
else:
    print(f"  {len(beats)} beats, {len(onsets)} onsets — single-pass processing")

def build_eq_expr(beats_subset, onsets_subset, chorus_ranges_active):
    """Build ffmpeg eq filter expressions for brightness and saturation."""
    # Brightness flash on onsets: +0.2 brightness with 0.1s decay
    bright_parts = []
    for onset in onsets_subset:
        bright_parts.append(
            f"if(between(t,{onset:.3f},{onset + 0.1:.3f}),0.2*(1-(t-{onset:.3f})/0.1),0)"
        )

    if bright_parts:
        brightness_expr = "+".join(bright_parts)
    else:
        brightness_expr = "0"

    # Saturation boost during chorus: +0.4 with 1s ramp
    sat_parts = ["1"]
    for start, end in chorus_ranges_active:
        ramp_end = start + 1.0
        sat_parts.append(
            f"if(between(t,{start:.3f},{ramp_end:.3f}),0.4*((t-{start:.3f})/1.0),"
            f"if(between(t,{ramp_end:.3f},{end:.3f}),0.4,0))"
        )

    saturation_expr = "+".join(sat_parts)

    return brightness_expr, saturation_expr

def build_zoom_expr(beats_subset):
    """Build zoom pulse expression: 3% scale bump with 0.15s decay."""
    parts = []
    for beat in beats_subset:
        parts.append(
            f"if(between(t,{beat:.3f},{beat + 0.15:.3f}),0.03*(1-(t-{beat:.3f})/0.15),0)"
        )
    if parts:
        return "+".join(parts)
    return "0"

if use_chunks:
    chunk_files = []
    for chunk_idx in range(num_chunks):
        chunk_start = chunk_idx * CHUNK_SIZE
        chunk_end = min((chunk_idx + 1) * CHUNK_SIZE, total_dur)

        # Filter beats/onsets for this chunk
        chunk_beats = [b for b in beats if chunk_start <= b < chunk_end]
        chunk_onsets = [o for o in onsets if chunk_start <= o < chunk_end]
        chunk_chorus = [(max(s, chunk_start), min(e, chunk_end))
                        for s, e in chorus_ranges if s < chunk_end and e > chunk_start]

        zoom_expr = build_zoom_expr(chunk_beats)
        bright_expr, sat_expr = build_eq_expr(chunk_beats, chunk_onsets, chunk_chorus)

        chunk_file = f"{tmp_dir}/chunk_{chunk_idx:03d}.mp4"
        chunk_files.append(chunk_file)

        # Extract chunk, apply effects, write
        zoom_w = f"iw*(1+({zoom_expr}))" if chunk_beats else "iw"
        zoom_h = f"ih*(1+({zoom_expr}))" if chunk_beats else "ih"

        vf = (
            f"scale='{zoom_w}:{zoom_h}',"
            f"crop=1920:1080:(iw-1920)/2:(ih-1080)/2,"
            f"eq=brightness='{bright_expr}':saturation='{sat_expr}'"
        )

        cmd = [
            "ffmpeg", "-y",
            "-ss", f"{chunk_start:.3f}",
            "-t", f"{chunk_end - chunk_start:.3f}",
            "-i", chained_video,
            "-vf", vf,
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-pix_fmt", "yuv420p", "-an",
            chunk_file
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"ERROR: Chunk {chunk_idx} failed:\n{result.stderr}", file=sys.stderr)
            sys.exit(1)

        print(f"    Chunk {chunk_idx + 1}/{num_chunks} done")

    # Concatenate chunks
    concat_list = f"{tmp_dir}/chunks.txt"
    with open(concat_list, "w") as f:
        for cf in chunk_files:
            f.write(f"file '{cf}'\n")

    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", concat_list,
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-pix_fmt", "yuv420p", "-an",
        output_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ERROR: Chunk concat failed:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)

else:
    # Single pass
    zoom_expr = build_zoom_expr(beats)
    bright_expr, sat_expr = build_eq_expr(beats, onsets, chorus_ranges)

    zoom_w = f"iw*(1+({zoom_expr}))" if beats else "iw"
    zoom_h = f"ih*(1+({zoom_expr}))" if beats else "ih"

    vf = (
        f"scale='{zoom_w}:{zoom_h}',"
        f"crop=1920:1080:(iw-1920)/2:(ih-1080)/2,"
        f"eq=brightness='{bright_expr}':saturation='{sat_expr}'"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", chained_video,
        "-vf", vf,
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-pix_fmt", "yuv420p", "-an",
        output_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ERROR: Beat effects failed:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)

print(f"  Beat-reactive effects applied → {output_path}")
PYEOF

  python3 "$EFFECTS_SCRIPT" \
    "$CHAINED_VIDEO" \
    "$BEAT_MAP" \
    "$SONG_STRUCTURE" \
    "$EFFECTS_VIDEO" \
    "$TMP_DIR"

  if [[ ! -f "$EFFECTS_VIDEO" ]]; then
    echo "ERROR: Beat effects video was not created" >&2
    exit 1
  fi
else
  log_step "Skipping beat-reactive effects (not enabled)"
  EFFECTS_VIDEO="$CHAINED_VIDEO"
fi

# ── Step 4: Overlay captions ───────────────────────────────────────────────

log_step "Overlaying captions (mode: $MODE)"

CAPTIONED_VIDEO="$TMP_DIR/captioned.mp4"

if [[ "$MODE" == "karaoke" ]]; then
  echo "  ASS subtitle overlay: $CAPTIONS"

  ffmpeg -y \
    -i "$EFFECTS_VIDEO" \
    -vf "ass=$CAPTIONS" \
    -c:v libx264 -preset fast -crf 18 \
    -pix_fmt yuv420p \
    -an \
    "$CAPTIONED_VIDEO" 2>/dev/null

elif [[ "$MODE" == "music-video" ]]; then
  echo "  PNG sequence overlay from: $CAPTIONS"

  # Build an image sequence input from the kinetic frames directory
  # Detect the frame pattern (e.g., frame_%04d.png)
  FIRST_FRAME=$(ls "$CAPTIONS"/*.png 2>/dev/null | head -1)
  if [[ -z "$FIRST_FRAME" ]]; then
    echo "ERROR: No PNG frames found in $CAPTIONS" >&2
    exit 1
  fi

  FRAME_COUNT=$(ls "$CAPTIONS"/*.png 2>/dev/null | wc -l | tr -d ' ')
  echo "  Found $FRAME_COUNT PNG frames"

  # Get video framerate to sync the image sequence
  VIDEO_FPS=$(ffprobe -v quiet -select_streams v:0 \
    -show_entries stream=r_frame_rate -of csv=p=0 "$EFFECTS_VIDEO" | head -1)
  echo "  Video FPS: $VIDEO_FPS"

  ffmpeg -y \
    -i "$EFFECTS_VIDEO" \
    -framerate "$VIDEO_FPS" -i "$CAPTIONS/%04d.png" \
    -filter_complex "[1:v]format=rgba[text];[0:v][text]overlay=0:0:format=auto:shortest=1" \
    -c:v libx264 -preset fast -crf 18 \
    -pix_fmt yuv420p \
    -an \
    "$CAPTIONED_VIDEO" 2>/dev/null
fi

if [[ ! -f "$CAPTIONED_VIDEO" ]]; then
  echo "ERROR: Captioned video was not created" >&2
  exit 1
fi

echo "  Captioned video: $(du -h "$CAPTIONED_VIDEO" | cut -f1)"

# ── Step 5: Mux audio ──────────────────────────────────────────────────────

log_step "Muxing original audio track"

echo "  Audio source: $AUDIO"

# Ensure output directory exists
OUTPUT_DIR="$(dirname "$OUTPUT")"
if [[ -n "$OUTPUT_DIR" && "$OUTPUT_DIR" != "." ]]; then
  mkdir -p "$OUTPUT_DIR"
fi

ffmpeg -y \
  -i "$CAPTIONED_VIDEO" \
  -i "$AUDIO" \
  -map 0:v:0 -map 1:a:0 \
  -c:v copy \
  -c:a aac -b:a 320k -ar 48000 \
  -movflags +faststart \
  -shortest \
  "$OUTPUT" 2>/dev/null

if [[ ! -f "$OUTPUT" ]]; then
  echo "ERROR: Final output was not created" >&2
  exit 1
fi

echo "  Final output: $OUTPUT"

# ── Step 6: Verify output ──────────────────────────────────────────────────

log_step "Verifying output with ffprobe"

echo "  File: $OUTPUT"
echo "  Size: $(du -h "$OUTPUT" | cut -f1)"
echo ""

ffprobe -v quiet -show_entries \
  "stream=codec_name,codec_type,width,height,r_frame_rate,bit_rate,sample_rate,channels" \
  -show_entries "format=duration,size,bit_rate" \
  -of json "$OUTPUT" | python3 -c "
import json, sys
data = json.load(sys.stdin)

for stream in data.get('streams', []):
    codec_type = stream.get('codec_type', 'unknown')
    codec_name = stream.get('codec_name', 'unknown')
    if codec_type == 'video':
        w = stream.get('width', '?')
        h = stream.get('height', '?')
        fps = stream.get('r_frame_rate', '?')
        print(f'  Video: {codec_name} {w}x{h} @ {fps} fps')
    elif codec_type == 'audio':
        sr = stream.get('sample_rate', '?')
        ch = stream.get('channels', '?')
        print(f'  Audio: {codec_name} {sr}Hz {ch}ch')

fmt = data.get('format', {})
dur = float(fmt.get('duration', 0))
size_mb = int(fmt.get('size', 0)) / (1024 * 1024)
bitrate_kbps = int(fmt.get('bit_rate', 0)) / 1000
print(f'  Duration: {dur:.1f}s')
print(f'  File size: {size_mb:.1f} MB')
print(f'  Bitrate: {bitrate_kbps:.0f} kbps')
"

# ── Done ────────────────────────────────────────────────────────────────────

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  Assembly complete: $OUTPUT"
echo "═══════════════════════════════════════════════════════════════"
echo ""
