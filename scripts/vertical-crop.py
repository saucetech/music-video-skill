#!/usr/bin/env python3
"""
Crop horizontal (16:9) music video clips to vertical (9:16) for TikTok/YouTube Shorts.

Supports three crop modes with graceful degradation:
  1. MediaPipe Pose detection (best) — tracks subject torso center
  2. OpenCV Haar cascade face detection — tracks face center
  3. Static center crop (fallback) — takes center 9:16 slice

Auto-selects the best moments from a music video using song structure and
beat map data, or crops a specific time range when --start/--end are provided.

Two-pass smart crop approach:
  Pass 1 (Python): detect subject center per frame → smooth → write sendcmd file
  Pass 2 (ffmpeg): apply dynamic crop via sendcmd + scale to 1080x1920

Usage:
    # Auto-select best clips
    python3 scripts/vertical-crop.py \\
      --video final-music-video.mp4 \\
      --song-structure song-structure.json \\
      --beat-map beat-map.json \\
      --output-dir ./tiktok-clips/ \\
      --num-clips 5 \\
      [--smart-crop]

    # Crop a specific time range
    python3 scripts/vertical-crop.py \\
      --video final.mp4 \\
      --start 45.0 --end 75.0 \\
      --output-dir ./tiktok-clips/ \\
      [--smart-crop]
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Detection backend discovery (graceful degradation)
# ---------------------------------------------------------------------------

DETECTION_BACKEND: str = "none"

try:
    import mediapipe as mp
    import numpy as np
    DETECTION_BACKEND = "mediapipe"
except ImportError:
    try:
        import cv2
        import numpy as np
        DETECTION_BACKEND = "opencv"
    except ImportError:
        pass


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

class Segment:
    """A candidate clip segment with timing and metadata."""

    def __init__(
        self,
        start: float,
        end: float,
        label: str = "",
        energy: float = 0.0,
    ):
        self.start = start
        self.end = end
        self.label = label
        self.energy = energy

    @property
    def duration(self) -> float:
        return self.end - self.start

    def overlaps(self, other: "Segment", margin: float = 2.0) -> bool:
        return self.start < (other.end + margin) and other.start < (self.end + margin)

    def to_dict(self) -> dict:
        return {
            "start_time": round(self.start, 3),
            "end_time": round(self.end, 3),
            "duration": round(self.duration, 3),
            "section_label": self.label,
            "energy": round(self.energy, 3),
        }

    def __repr__(self) -> str:
        return f"Segment({self.label}, {self.start:.1f}-{self.end:.1f}, energy={self.energy:.2f})"


# ---------------------------------------------------------------------------
# Probe helpers
# ---------------------------------------------------------------------------

def probe_video(video_path: str) -> dict:
    """Get video dimensions, duration, and fps via ffprobe."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_streams", "-show_format",
        video_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    info = json.loads(result.stdout)

    video_stream = None
    for stream in info.get("streams", []):
        if stream.get("codec_type") == "video":
            video_stream = stream
            break

    if not video_stream:
        raise RuntimeError(f"No video stream found in {video_path}")

    # Parse fps from r_frame_rate (e.g. "30/1" or "30000/1001")
    fps_parts = video_stream.get("r_frame_rate", "30/1").split("/")
    fps = float(fps_parts[0]) / float(fps_parts[1]) if len(fps_parts) == 2 else 30.0

    return {
        "width": int(video_stream["width"]),
        "height": int(video_stream["height"]),
        "fps": fps,
        "duration": float(info.get("format", {}).get("duration", 0)),
    }


# ---------------------------------------------------------------------------
# Segment selection from song structure + beat map
# ---------------------------------------------------------------------------

def snap_to_beat(time: float, beats: list[float], direction: str = "nearest") -> float:
    """Snap a timestamp to the nearest beat."""
    if not beats:
        return time

    if direction == "before":
        candidates = [b for b in beats if b <= time + 0.01]
        return max(candidates) if candidates else beats[0]
    elif direction == "after":
        candidates = [b for b in beats if b >= time - 0.01]
        return min(candidates) if candidates else beats[-1]
    else:
        return min(beats, key=lambda b: abs(b - time))


def load_song_structure(path: str) -> list[dict]:
    """Load song structure JSON — expects a list of sections with start, end, label."""
    with open(path, "r") as f:
        data = json.load(f)

    # Handle both flat list and nested {"sections": [...]} formats
    if isinstance(data, dict):
        sections = data.get("sections", data.get("segments", []))
    else:
        sections = data

    return sections


def load_beat_map(path: str) -> list[float]:
    """Load beat map JSON — expects a list of beat timestamps or {"beats": [...]}."""
    with open(path, "r") as f:
        data = json.load(f)

    if isinstance(data, dict):
        return data.get("beats", data.get("timestamps", []))
    elif isinstance(data, list):
        # Could be list of floats or list of objects with "time" key
        if data and isinstance(data[0], (int, float)):
            return [float(b) for b in data]
        else:
            return [float(b.get("time", b.get("timestamp", 0))) for b in data]
    return []


def compute_section_energy(section: dict, beats: list[float]) -> float:
    """
    Estimate energy for a song section.

    Uses a combination of:
    - Section type priority (chorus > bridge > verse)
    - Beat density within the section
    - Explicit energy/intensity if provided in the JSON
    """
    start = float(section.get("start", 0))
    end = float(section.get("end", start + 30))
    label = section.get("label", section.get("name", "")).lower()

    # Base energy from section type
    type_energy = {
        "chorus": 0.9,
        "drop": 0.95,
        "climax": 0.95,
        "bridge": 0.7,
        "prechorus": 0.65,
        "pre-chorus": 0.65,
        "verse": 0.5,
        "intro": 0.4,
        "outro": 0.3,
    }

    base = 0.5
    for keyword, value in type_energy.items():
        if keyword in label:
            base = value
            break

    # Use explicit energy/intensity if present
    explicit = section.get("energy", section.get("intensity", None))
    if explicit is not None:
        base = (base + float(explicit)) / 2

    # Beat density bonus (normalized beats per second)
    section_beats = [b for b in beats if start <= b <= end]
    duration = max(end - start, 0.1)
    beat_density = len(section_beats) / duration
    # Normalize: typical pop is 2 bps, high energy is 4+
    density_bonus = min(beat_density / 4.0, 0.3)

    return min(base + density_bonus, 1.0)


def select_best_segments(
    structure_path: str,
    beat_map_path: str,
    num_clips: int,
    min_duration: float = 15.0,
    max_duration: float = 30.0,
) -> list[Segment]:
    """Select the best non-overlapping segments for vertical clips."""
    sections = load_song_structure(structure_path)
    beats = load_beat_map(beat_map_path)

    if not sections:
        raise ValueError("No sections found in song structure file")

    # Score each section
    scored: list[Segment] = []
    first_chorus_found = False

    for i, section in enumerate(sections):
        start = float(section.get("start", 0))
        end = float(section.get("end", start + 30))
        label = section.get("label", section.get("name", f"section-{i}"))
        energy = compute_section_energy(section, beats)

        # First chorus bonus — usually the best hook for a clip
        label_lower = label.lower()
        if "chorus" in label_lower and not first_chorus_found:
            energy = min(energy + 0.15, 1.0)
            first_chorus_found = True

        # Strong opener bonus (first 30s)
        if start < 5.0 and energy >= 0.4:
            energy = min(energy + 0.1, 1.0)

        # Clip the segment to target duration range
        raw_duration = end - start
        if raw_duration < min_duration:
            # Too short — try extending to the next section boundary
            end = start + min_duration
        elif raw_duration > max_duration:
            # Too long — take the most energetic sub-window
            # For simplicity, take from start (hooks usually front-loaded)
            end = start + max_duration

        # Snap to beats
        snapped_start = snap_to_beat(start, beats, direction="before")
        snapped_end = snap_to_beat(end, beats, direction="after")

        # Ensure we stay within duration bounds after snapping
        if snapped_end - snapped_start > max_duration + 1.0:
            snapped_end = snap_to_beat(snapped_start + max_duration, beats, direction="before")
        if snapped_end - snapped_start < min_duration - 1.0:
            snapped_end = snap_to_beat(snapped_start + min_duration, beats, direction="after")

        scored.append(Segment(
            start=max(snapped_start, 0),
            end=snapped_end,
            label=label,
            energy=energy,
        ))

    # Sort by energy descending
    scored.sort(key=lambda s: s.energy, reverse=True)

    # Greedily pick non-overlapping segments
    selected: list[Segment] = []
    for seg in scored:
        if len(selected) >= num_clips:
            break
        if not any(seg.overlaps(s) for s in selected):
            selected.append(seg)

    # Sort selected by start time for sequential output
    selected.sort(key=lambda s: s.start)

    log(f"Selected {len(selected)} segments from {len(scored)} candidates")
    for seg in selected:
        log(f"  {seg}")

    return selected


# ---------------------------------------------------------------------------
# Smart crop — subject detection
# ---------------------------------------------------------------------------

def detect_subject_centers_mediapipe(
    video_path: str,
    start: float,
    end: float,
    width: int,
    height: int,
    fps: float,
) -> list[Optional[int]]:
    """
    Use MediaPipe Pose to detect subject torso center X per frame.

    Returns a list of X positions (or None for frames with no detection).
    """
    import mediapipe as _mp
    import cv2
    import numpy as _np

    pose = _mp.solutions.pose.Pose(
        static_image_mode=False,
        model_complexity=1,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5,
    )

    cap = cv2.VideoCapture(video_path)
    cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000)

    centers: list[Optional[int]] = []
    total_frames = int((end - start) * fps)
    processed = 0

    while cap.isOpened():
        current_time = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
        if current_time > end + 0.1:
            break

        ret, frame = cap.read()
        if not ret:
            break

        # Convert BGR to RGB for MediaPipe
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = pose.process(rgb)

        if results.pose_landmarks:
            landmarks = results.pose_landmarks.landmark
            # Torso center: average of left/right shoulder and left/right hip
            # Landmark indices: 11=left_shoulder, 12=right_shoulder, 23=left_hip, 24=right_hip
            torso_points = [landmarks[11], landmarks[12], landmarks[23], landmarks[24]]
            avg_x = sum(p.x for p in torso_points) / len(torso_points)
            center_x = int(avg_x * width)
            centers.append(center_x)
        else:
            centers.append(None)

        processed += 1
        if processed % 100 == 0:
            log(f"  MediaPipe: processed {processed}/{total_frames} frames")

    cap.release()
    pose.close()

    log(f"  MediaPipe: {sum(1 for c in centers if c is not None)}/{len(centers)} frames with detections")
    return centers


def detect_subject_centers_opencv(
    video_path: str,
    start: float,
    end: float,
    width: int,
    height: int,
    fps: float,
) -> list[Optional[int]]:
    """
    Use OpenCV Haar cascade face detection as fallback.

    Returns a list of X positions (or None for frames with no detection).
    """
    import cv2

    # Try to find the Haar cascade file
    cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    face_cascade = cv2.CascadeClassifier(cascade_path)

    if face_cascade.empty():
        log("  WARNING: Could not load Haar cascade, falling back to center crop")
        return []

    cap = cv2.VideoCapture(video_path)
    cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000)

    centers: list[Optional[int]] = []
    total_frames = int((end - start) * fps)
    processed = 0

    while cap.isOpened():
        current_time = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
        if current_time > end + 0.1:
            break

        ret, frame = cap.read()
        if not ret:
            break

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = face_cascade.detectMultiScale(
            gray,
            scaleFactor=1.1,
            minNeighbors=5,
            minSize=(50, 50),
        )

        if len(faces) > 0:
            # Pick the largest face
            largest = max(faces, key=lambda f: f[2] * f[3])
            face_center_x = largest[0] + largest[2] // 2
            centers.append(face_center_x)
        else:
            centers.append(None)

        processed += 1
        if processed % 100 == 0:
            log(f"  OpenCV: processed {processed}/{total_frames} frames")

    cap.release()

    log(f"  OpenCV: {sum(1 for c in centers if c is not None)}/{len(centers)} frames with detections")
    return centers


def smooth_crop_positions(
    centers: list[Optional[int]],
    video_width: int,
    crop_width: int,
    window: int = 15,
) -> list[int]:
    """
    Smooth raw detection centers into stable crop X positions.

    1. Forward-fill None values from last known detection
    2. Apply rolling average to remove jitter
    3. Clamp to valid crop bounds
    """
    default_center = video_width // 2

    # Forward-fill None values
    filled: list[int] = []
    last_known = default_center
    for c in centers:
        if c is not None:
            last_known = c
        filled.append(last_known)

    # Also backward-fill leading Nones
    first_valid = default_center
    for c in centers:
        if c is not None:
            first_valid = c
            break
    for i in range(len(filled)):
        if centers[i] is not None:
            break
        filled[i] = first_valid

    # Rolling average for smoothness
    smoothed: list[int] = []
    half = window // 2
    for i in range(len(filled)):
        lo = max(0, i - half)
        hi = min(len(filled), i + half + 1)
        avg = sum(filled[lo:hi]) / (hi - lo)
        smoothed.append(int(avg))

    # Convert center X to crop X (top-left) and clamp
    max_x = video_width - crop_width
    crop_positions: list[int] = []
    for cx in smoothed:
        crop_x = cx - crop_width // 2
        crop_x = max(0, min(crop_x, max_x))
        crop_positions.append(crop_x)

    return crop_positions


def write_sendcmd_file(
    crop_positions: list[int],
    crop_width: int,
    crop_height: int,
    fps: float,
    output_path: str,
) -> None:
    """
    Write an ffmpeg sendcmd script for dynamic crop positioning.

    Each command sets the crop x offset at the corresponding frame time.
    We only write commands when the position changes to keep the file small.
    """
    with open(output_path, "w") as f:
        prev_x = -1
        for i, crop_x in enumerate(crop_positions):
            if crop_x != prev_x:
                time = i / fps
                f.write(f"{time:.4f} [enter] crop w {crop_width} h {crop_height} x {crop_x} y 0;\n")
                prev_x = crop_x

    log(f"  Wrote sendcmd file: {output_path}")


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------

def encode_static_crop(
    video_path: str,
    output_path: str,
    start: float,
    end: float,
) -> None:
    """Encode a clip with static center crop (no subject tracking)."""
    duration = end - start

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start),
        "-t", str(duration),
        "-i", video_path,
        "-vf", "crop=ih*9/16:ih,scale=1080:1920",
        "-c:v", "libx264",
        "-profile:v", "high",
        "-level:v", "4.1",
        "-crf", "18",
        "-preset", "slow",
        "-r", "30",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-ar", "44100",
        "-movflags", "+faststart",
        output_path,
    ]

    log(f"  Encoding (static crop): {output_path}")
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def encode_smart_crop(
    video_path: str,
    output_path: str,
    start: float,
    end: float,
    sendcmd_path: str,
    crop_width: int,
    crop_height: int,
) -> None:
    """Encode a clip with dynamic crop via sendcmd (subject tracking)."""
    duration = end - start

    # The sendcmd filter reads commands and applies them to the crop filter
    vf = (
        f"sendcmd=f='{sendcmd_path}',"
        f"crop={crop_width}:{crop_height}:0:0,"
        f"scale=1080:1920"
    )

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start),
        "-t", str(duration),
        "-i", video_path,
        "-vf", vf,
        "-c:v", "libx264",
        "-profile:v", "high",
        "-level:v", "4.1",
        "-crf", "18",
        "-preset", "slow",
        "-r", "30",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-ar", "44100",
        "-movflags", "+faststart",
        output_path,
    ]

    log(f"  Encoding (smart crop): {output_path}")
    subprocess.run(cmd, check=True, capture_output=True, text=True)


# ---------------------------------------------------------------------------
# Main processing pipeline
# ---------------------------------------------------------------------------

def process_clip(
    video_path: str,
    segment: Segment,
    output_path: str,
    smart_crop: bool,
    video_info: dict,
    temp_dir: str,
) -> dict:
    """
    Process a single clip: detect subject (if smart crop), encode, return metadata.
    """
    width = video_info["width"]
    height = video_info["height"]
    fps = video_info["fps"]

    # 9:16 crop dimensions from original resolution
    crop_height = height
    crop_width = int(height * 9 / 16)

    if crop_width > width:
        # Video is already narrower than 9:16 — just scale
        log(f"  Video is narrower than 9:16, using full width")
        smart_crop = False

    if smart_crop and DETECTION_BACKEND != "none":
        log(f"  Running subject detection ({DETECTION_BACKEND})...")

        # Pass 1: detect subject centers
        if DETECTION_BACKEND == "mediapipe":
            centers = detect_subject_centers_mediapipe(
                video_path, segment.start, segment.end,
                width, height, fps,
            )
        else:
            centers = detect_subject_centers_opencv(
                video_path, segment.start, segment.end,
                width, height, fps,
            )

        if centers and any(c is not None for c in centers):
            # Smooth and write sendcmd
            crop_positions = smooth_crop_positions(centers, width, crop_width)
            clip_name = Path(output_path).stem
            sendcmd_path = os.path.join(temp_dir, f"{clip_name}_sendcmd.txt")
            write_sendcmd_file(crop_positions, crop_width, crop_height, fps, sendcmd_path)

            # Pass 2: encode with dynamic crop
            encode_smart_crop(
                video_path, output_path,
                segment.start, segment.end,
                sendcmd_path, crop_width, crop_height,
            )
        else:
            log(f"  No subject detected, falling back to static center crop")
            encode_static_crop(video_path, output_path, segment.start, segment.end)
    else:
        if smart_crop and DETECTION_BACKEND == "none":
            log(f"  WARNING: --smart-crop requested but no detection library available")
            log(f"           Install mediapipe (pip install mediapipe) or opencv-python")
        encode_static_crop(video_path, output_path, segment.start, segment.end)

    # Build metadata
    meta = segment.to_dict()
    meta["file"] = os.path.basename(output_path)
    meta["crop_mode"] = (
        DETECTION_BACKEND if (smart_crop and DETECTION_BACKEND != "none") else "center"
    )

    return meta


def log(msg: str) -> None:
    """Print a timestamped log message to stderr."""
    print(f"[vertical-crop] {msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Crop horizontal music video clips to vertical (9:16) for TikTok/Shorts",
    )

    parser.add_argument(
        "--video", required=True,
        help="Path to the source video file",
    )
    parser.add_argument(
        "--output-dir", required=True,
        help="Directory for output clips and manifest",
    )

    # Auto-select mode
    parser.add_argument(
        "--song-structure",
        help="Path to song-structure.json (required for auto-select)",
    )
    parser.add_argument(
        "--beat-map",
        help="Path to beat-map.json (required for auto-select)",
    )
    parser.add_argument(
        "--num-clips", type=int, default=5,
        help="Number of clips to generate in auto-select mode (default: 5)",
    )

    # Manual range mode
    parser.add_argument(
        "--start", type=float,
        help="Start time in seconds (for single clip mode)",
    )
    parser.add_argument(
        "--end", type=float,
        help="End time in seconds (for single clip mode)",
    )

    # Crop mode
    parser.add_argument(
        "--smart-crop", action="store_true",
        help="Enable subject-aware tracking (requires mediapipe or opencv)",
    )

    # Duration constraints for auto-select
    parser.add_argument(
        "--min-duration", type=float, default=15.0,
        help="Minimum clip duration in seconds (default: 15)",
    )
    parser.add_argument(
        "--max-duration", type=float, default=30.0,
        help="Maximum clip duration in seconds (default: 30)",
    )

    args = parser.parse_args()

    # Validate: either auto-select or manual range
    manual_mode = args.start is not None or args.end is not None
    auto_mode = args.song_structure is not None or args.beat_map is not None

    if manual_mode:
        if args.start is None or args.end is None:
            parser.error("Both --start and --end are required for manual range mode")
        if args.end <= args.start:
            parser.error("--end must be greater than --start")
    elif auto_mode:
        if args.song_structure is None or args.beat_map is None:
            parser.error("Both --song-structure and --beat-map are required for auto-select mode")
    else:
        parser.error(
            "Provide either --song-structure + --beat-map (auto-select) "
            "or --start + --end (manual range)"
        )

    return args


def main() -> None:
    args = parse_args()

    # Validate input video
    if not os.path.isfile(args.video):
        log(f"ERROR: Video file not found: {args.video}")
        sys.exit(1)

    # Create output directory
    os.makedirs(args.output_dir, exist_ok=True)

    # Probe video
    log(f"Probing video: {args.video}")
    video_info = probe_video(args.video)
    log(f"  Resolution: {video_info['width']}x{video_info['height']}")
    log(f"  FPS: {video_info['fps']:.2f}")
    log(f"  Duration: {video_info['duration']:.2f}s")

    # Determine detection backend
    if args.smart_crop:
        log(f"Smart crop enabled — detection backend: {DETECTION_BACKEND}")
        if DETECTION_BACKEND == "none":
            log("WARNING: No detection library found. Will use static center crop.")
            log("  Install one of: pip install mediapipe  OR  pip install opencv-python")

    # Build segment list
    manual_mode = args.start is not None and args.end is not None

    if manual_mode:
        segments = [Segment(
            start=args.start,
            end=args.end,
            label="manual",
            energy=1.0,
        )]
    else:
        segments = select_best_segments(
            args.song_structure,
            args.beat_map,
            args.num_clips,
            min_duration=args.min_duration,
            max_duration=args.max_duration,
        )

    if not segments:
        log("ERROR: No segments selected")
        sys.exit(1)

    # Process each clip
    manifest: list[dict] = []

    with tempfile.TemporaryDirectory(prefix="vertical-crop-") as temp_dir:
        for i, segment in enumerate(segments):
            clip_num = i + 1
            output_name = f"tiktok-clip-{clip_num:02d}.mp4"
            output_path = os.path.join(args.output_dir, output_name)

            log(f"\nClip {clip_num}/{len(segments)}: {segment.label} "
                f"({segment.start:.1f}s - {segment.end:.1f}s, "
                f"energy={segment.energy:.2f})")

            try:
                meta = process_clip(
                    video_path=args.video,
                    segment=segment,
                    output_path=output_path,
                    smart_crop=args.smart_crop,
                    video_info=video_info,
                    temp_dir=temp_dir,
                )
                manifest.append(meta)
                log(f"  Done: {output_name}")
            except subprocess.CalledProcessError as e:
                log(f"  ERROR encoding clip {clip_num}: {e.stderr or e}")
                continue
            except Exception as e:
                log(f"  ERROR processing clip {clip_num}: {e}")
                continue

    # Write manifest
    manifest_path = os.path.join(args.output_dir, "clips-manifest.json")
    with open(manifest_path, "w") as f:
        json.dump({
            "source_video": os.path.basename(args.video),
            "output_format": "1080x1920 H.264 30fps",
            "crop_mode": DETECTION_BACKEND if args.smart_crop else "center",
            "clips": manifest,
        }, f, indent=2)

    log(f"\nComplete: {len(manifest)} clips written to {args.output_dir}")
    log(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
