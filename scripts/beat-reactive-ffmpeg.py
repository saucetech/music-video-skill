#!/usr/bin/env python3
"""
beat-reactive-ffmpeg.py — Generate ffmpeg filtergraph strings for beat-reactive visual effects.

Given a beat-map JSON (beats + onsets) and song-structure JSON (sections),
produces ffmpeg commands that apply:
  1. Zoom pulse on beats (3% scale, 0.15s decay)
  2. Brightness flash on onsets (0.2 boost, 0.1s decay)
  3. Saturation boost during choruses (+0.4, 1s ramp)

Automatically chunks into 30s segments when expression length exceeds ffmpeg limits.
"""

import argparse
import json
import logging
import math
import os
import subprocess
import sys
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ffmpeg expression strings have a practical limit around 8-16KB.
# We use 8000 as a conservative threshold to trigger chunking.
EXPR_LENGTH_LIMIT = 8000
CHUNK_DURATION = 30.0  # seconds per segment when chunking


# ---------------------------------------------------------------------------
# Expression builders
# ---------------------------------------------------------------------------

def build_zoom_expr(beats: list[float]) -> str:
    """
    Build an ffmpeg expression for zoom scale factor.

    Each beat produces a 3% bump that decays exponentially over 0.15s.
    The base scale is 1.0; each beat adds:
      0.03 * max(0, 1 - (t - BEAT) / 0.15) * between(t, BEAT, BEAT + 0.15)

    Returns the full scale expression string (e.g. "1.0+0.03*max(...)+...").
    """
    if not beats:
        return "1.0"

    terms: list[str] = []
    for b in beats:
        end = round(b + 0.15, 4)
        terms.append(
            f"0.03*max(0,1-(t-{b})/{0.15})*between(t,{b},{end})"
        )

    return "1.0+" + "+".join(terms)


def build_brightness_expr(onsets: list[float]) -> str:
    """
    Build an ffmpeg expression for brightness adjustment.

    Each onset produces a 0.2 brightness boost that decays over 0.1s.
    Returns the expression string (summed terms, no base — added to eq default of 0).
    """
    if not onsets:
        return "0"

    terms: list[str] = []
    for o in onsets:
        end = round(o + 0.1, 4)
        terms.append(
            f"0.2*max(0,1-(t-{o})/{0.1})*between(t,{o},{end})"
        )

    return "+".join(terms)


def build_saturation_expr(choruses: list[dict]) -> str:
    """
    Build an ffmpeg expression for saturation boost during chorus sections.

    Each chorus gets +0.4 saturation with a 1s ramp in and 1s ramp out.
    Returns the expression to add to base saturation of 1.0.
    """
    if not choruses:
        return "1.0"

    terms: list[str] = []
    for section in choruses:
        start = section["start"]
        end = section["end"]
        # clip((t-START)/1.0, 0, 1) ramps in over 1s
        # clip((END-t)/1.0, 0, 1) ramps out over 1s
        terms.append(
            f"0.4*clip((t-{start})/1.0,0,1)*clip(({end}-t)/1.0,0,1)"
        )

    return "1.0+" + "+".join(terms)


# ---------------------------------------------------------------------------
# Filtergraph assembly
# ---------------------------------------------------------------------------

def extract_choruses(sections: list[dict]) -> list[dict]:
    """Pull out chorus sections from the song structure."""
    return [s for s in sections if s.get("label", "").lower() == "chorus"]


def filter_events_for_window(
    events: list[float], start: float, end: float
) -> list[float]:
    """Return events that fall within [start, end)."""
    return [e for e in events if start <= e < end]


def filter_choruses_for_window(
    choruses: list[dict], start: float, end: float
) -> list[dict]:
    """Return choruses that overlap with [start, end)."""
    result = []
    for c in choruses:
        if c["end"] > start and c["start"] < end:
            result.append({
                "start": max(c["start"], start),
                "end": min(c["end"], end),
            })
    return result


def build_filtergraph(
    beats: list[float],
    onsets: list[float],
    choruses: list[dict],
    width: int,
    height: int,
    enabled_effects: set[str],
) -> str:
    """
    Build the complete ffmpeg filtergraph string for a single segment.

    Filter ordering:
      1. scale + crop (zoom) — changes resolution, do first
      2. eq (brightness + saturation merged) — pixel-level

    The scale filter uses trunc(...)/2)*2 to guarantee even dimensions.
    """
    filters: list[str] = []

    # 1. Zoom pulse
    if "zoom" in enabled_effects and beats:
        zoom_expr = build_zoom_expr(beats)
        # Scale up by zoom factor, then crop back to original resolution.
        # trunc(dimension * zoom / 2) * 2 ensures even dimensions.
        scale_w = f"trunc(iw*({zoom_expr})/2)*2"
        scale_h = f"trunc(ih*({zoom_expr})/2)*2"
        filters.append(f"scale={scale_w}:{scale_h}")
        # Crop back to original size, centered
        filters.append(
            f"crop={width}:{height}:(iw-{width})/2:(ih-{height})/2"
        )

    # 2. Merged eq filter (brightness + saturation)
    eq_parts: list[str] = []

    if "flash" in enabled_effects and onsets:
        brightness_expr = build_brightness_expr(onsets)
        eq_parts.append(f"brightness='{brightness_expr}'")

    if "saturation" in enabled_effects and choruses:
        saturation_expr = build_saturation_expr(choruses)
        eq_parts.append(f"saturation='{saturation_expr}'")

    if eq_parts:
        filters.append("eq=" + ":".join(eq_parts))

    return ",".join(filters) if filters else "null"


def estimate_expression_length(
    beats: list[float],
    onsets: list[float],
    choruses: list[dict],
    enabled_effects: set[str],
) -> int:
    """
    Estimate the total character length of all expressions combined.

    Used to decide whether chunking is needed.
    """
    total = 0

    if "zoom" in enabled_effects:
        # ~55 chars per beat term + overhead
        total += len(beats) * 60 + 50

    if "flash" in enabled_effects:
        # ~50 chars per onset term + overhead
        total += len(onsets) * 55 + 50

    if "saturation" in enabled_effects:
        # ~55 chars per chorus term + overhead
        total += len(choruses) * 60 + 50

    return total


def needs_chunking(
    beats: list[float],
    onsets: list[float],
    choruses: list[dict],
    enabled_effects: set[str],
) -> bool:
    """
    Determine if the video needs to be processed in chunks.

    Triggers when beat/onset count exceeds 150 or estimated expression
    length exceeds the ffmpeg practical limit.
    """
    event_count = 0
    if "zoom" in enabled_effects:
        event_count += len(beats)
    if "flash" in enabled_effects:
        event_count += len(onsets)

    if event_count > 150:
        log.info(
            "Chunking required: %d events exceed 150-event threshold",
            event_count,
        )
        return True

    est = estimate_expression_length(beats, onsets, choruses, enabled_effects)
    if est > EXPR_LENGTH_LIMIT:
        log.info(
            "Chunking required: estimated expression length %d > %d",
            est,
            EXPR_LENGTH_LIMIT,
        )
        return True

    return False


# ---------------------------------------------------------------------------
# Video duration probe
# ---------------------------------------------------------------------------

def probe_video(input_path: str) -> tuple[float, int, int]:
    """
    Use ffprobe to get duration, width, and height of the input video.

    Returns (duration_seconds, width, height).
    """
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,duration",
        "-show_entries", "format=duration",
        "-of", "json",
        input_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    info = json.loads(result.stdout)

    # Width/height from stream
    stream = info.get("streams", [{}])[0]
    width = int(stream.get("width", 1920))
    height = int(stream.get("height", 1080))

    # Duration: prefer stream, fall back to format
    duration = float(
        stream.get("duration")
        or info.get("format", {}).get("duration", "0")
    )

    return duration, width, height


# ---------------------------------------------------------------------------
# Command builders
# ---------------------------------------------------------------------------

def build_single_command(
    input_path: str,
    output_path: str,
    filtergraph: str,
) -> list[str]:
    """Build a single ffmpeg command with the given filtergraph."""
    return [
        "ffmpeg", "-y",
        "-i", input_path,
        "-vf", filtergraph,
        "-c:a", "copy",
        output_path,
    ]


def build_chunked_commands(
    input_path: str,
    output_path: str,
    beats: list[float],
    onsets: list[float],
    choruses: list[dict],
    duration: float,
    width: int,
    height: int,
    enabled_effects: set[str],
) -> tuple[list[list[str]], list[str]]:
    """
    Build chunked ffmpeg commands for long videos.

    Splits the video into CHUNK_DURATION-second segments, builds a filtergraph
    for each with only the events in that window, then concatenates.

    Returns (commands_list, temp_file_paths) so the caller can execute
    them in sequence and clean up temp files afterward.
    """
    num_chunks = math.ceil(duration / CHUNK_DURATION)
    log.info("Splitting into %d chunks of %.1fs each", num_chunks, CHUNK_DURATION)

    output_dir = os.path.dirname(os.path.abspath(output_path))
    base_name = os.path.splitext(os.path.basename(output_path))[0]

    commands: list[list[str]] = []
    temp_files: list[str] = []
    segment_list_path = os.path.join(output_dir, f"{base_name}_segments.txt")

    segment_entries: list[str] = []

    for i in range(num_chunks):
        chunk_start = i * CHUNK_DURATION
        chunk_end = min((i + 1) * CHUNK_DURATION, duration)

        # Filter events to this chunk's time window
        chunk_beats = filter_events_for_window(beats, chunk_start, chunk_end)
        chunk_onsets = filter_events_for_window(onsets, chunk_start, chunk_end)
        chunk_choruses = filter_choruses_for_window(choruses, chunk_start, chunk_end)

        filtergraph = build_filtergraph(
            chunk_beats, chunk_onsets, chunk_choruses,
            width, height, enabled_effects,
        )

        segment_path = os.path.join(
            output_dir, f"{base_name}_chunk{i:04d}.mp4"
        )
        temp_files.append(segment_path)
        segment_entries.append(f"file '{segment_path}'")

        cmd = [
            "ffmpeg", "-y",
            "-ss", str(chunk_start),
            "-t", str(chunk_end - chunk_start),
            "-i", input_path,
            "-vf", filtergraph,
            "-c:a", "copy",
            segment_path,
        ]
        commands.append(cmd)

    # Write concat list file
    temp_files.append(segment_list_path)
    with open(segment_list_path, "w") as f:
        f.write("\n".join(segment_entries) + "\n")

    # Concat command
    concat_cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", segment_list_path,
        "-c", "copy",
        output_path,
    ]
    commands.append(concat_cmd)

    return commands, temp_files


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

def run_commands(
    commands: list[list[str]],
    dry_run: bool = False,
    temp_files: Optional[list[str]] = None,
) -> None:
    """Execute a list of ffmpeg commands, or print them in dry-run mode."""
    for i, cmd in enumerate(commands, 1):
        cmd_str = " ".join(cmd)
        if dry_run:
            print(cmd_str)
        else:
            log.info("Running command %d/%d ...", i, len(commands))
            log.debug("%s", cmd_str)
            subprocess.run(cmd, check=True)
            log.info("Command %d/%d complete", i, len(commands))

    # Clean up temp files after successful execution
    if not dry_run and temp_files:
        for path in temp_files:
            if os.path.exists(path):
                os.remove(path)
                log.debug("Removed temp file: %s", path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate beat-reactive ffmpeg filtergraphs from a beat map.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  # Dry-run: print the ffmpeg command without executing
  python3 beat-reactive-ffmpeg.py \\
    --input video.mp4 --beat-map beat-map.json \\
    --song-structure song-structure.json \\
    --output video-fx.mp4 --dry-run

  # Output only the filtergraph string
  python3 beat-reactive-ffmpeg.py \\
    --beat-map beat-map.json \\
    --song-structure song-structure.json \\
    --filtergraph-only

  # Execute with only zoom and flash effects
  python3 beat-reactive-ffmpeg.py \\
    --input video.mp4 --beat-map beat-map.json \\
    --song-structure song-structure.json \\
    --output video-fx.mp4 --effects zoom,flash
""",
    )

    parser.add_argument(
        "--input",
        help="Input video file path (required unless --filtergraph-only)",
    )
    parser.add_argument(
        "--beat-map",
        required=True,
        help="Path to beat-map JSON (beats + onsets)",
    )
    parser.add_argument(
        "--song-structure",
        required=True,
        help="Path to song-structure JSON (sections with labels)",
    )
    parser.add_argument(
        "--output",
        help="Output video file path (required unless --filtergraph-only or --dry-run)",
    )
    parser.add_argument(
        "--effects",
        default="zoom,flash,saturation",
        help="Comma-separated effects to enable (default: zoom,flash,saturation)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print ffmpeg command(s) without executing",
    )
    parser.add_argument(
        "--filtergraph-only",
        action="store_true",
        help="Print only the filtergraph string (no ffmpeg command)",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=None,
        help="Video width override (auto-detected from input if omitted)",
    )
    parser.add_argument(
        "--height",
        type=int,
        default=None,
        help="Video height override (auto-detected from input if omitted)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable debug logging",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Validate argument combinations
    if not args.filtergraph_only and not args.input:
        log.error("--input is required unless --filtergraph-only is set")
        sys.exit(1)

    if not args.filtergraph_only and not args.dry_run and not args.output:
        log.error("--output is required unless --filtergraph-only or --dry-run is set")
        sys.exit(1)

    # Parse enabled effects
    valid_effects = {"zoom", "flash", "saturation"}
    enabled_effects = {e.strip().lower() for e in args.effects.split(",")}
    unknown = enabled_effects - valid_effects
    if unknown:
        log.error("Unknown effects: %s (valid: %s)", unknown, valid_effects)
        sys.exit(1)

    log.info("Enabled effects: %s", ", ".join(sorted(enabled_effects)))

    # Load beat map
    with open(args.beat_map) as f:
        beat_map = json.load(f)

    beats: list[float] = beat_map.get("beats", [])
    onsets: list[float] = beat_map.get("onsets", [])
    log.info("Loaded beat map: %d beats, %d onsets, %.1f BPM",
             len(beats), len(onsets), beat_map.get("bpm", 0))

    # Load song structure
    with open(args.song_structure) as f:
        song_structure = json.load(f)

    sections: list[dict] = song_structure.get("sections", [])
    choruses = extract_choruses(sections)
    log.info("Loaded song structure: %d sections, %d choruses",
             len(sections), len(choruses))

    # Resolve video dimensions
    duration = 0.0
    width = args.width or 1920
    height = args.height or 1080

    if args.input and not args.filtergraph_only:
        try:
            duration, probed_w, probed_h = probe_video(args.input)
            if not args.width:
                width = probed_w
            if not args.height:
                height = probed_h
            log.info("Video: %.1fs, %dx%d", duration, width, height)
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            log.warning("Could not probe video: %s — using defaults", exc)

    # -- Filtergraph-only mode --
    if args.filtergraph_only:
        fg = build_filtergraph(
            beats, onsets, choruses, width, height, enabled_effects,
        )
        print(fg)
        return

    # -- Check if chunking is needed --
    if needs_chunking(beats, onsets, choruses, enabled_effects) and duration > 0:
        commands, temp_files = build_chunked_commands(
            args.input,
            args.output or "output.mp4",
            beats, onsets, choruses,
            duration, width, height,
            enabled_effects,
        )
        run_commands(commands, dry_run=args.dry_run, temp_files=temp_files)
    else:
        fg = build_filtergraph(
            beats, onsets, choruses, width, height, enabled_effects,
        )
        log.info("Filtergraph length: %d chars", len(fg))
        cmd = build_single_command(
            args.input,
            args.output or "output.mp4",
            fg,
        )
        run_commands([cmd], dry_run=args.dry_run)

    if not args.dry_run:
        log.info("Done — output: %s", args.output)


if __name__ == "__main__":
    main()
