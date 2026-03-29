#!/usr/bin/env python3
"""
QA Check — Automated quality assurance for AI-generated music video clips.

Three tiers:
  1. Face consistency (DeepFace + ArcFace, optional)
  2. Basic video quality (ffprobe + SSIM via OpenCV)
  3. Color consistency across clips (LAB histogram correlation)

Usage:
  python3 scripts/qa-check.py --clip scene-01.mp4 --reference-face char.png --output qa.json
  python3 scripts/qa-check.py --clips-dir ./clips/ --reference-face char.png --output qa.json
  python3 scripts/qa-check.py --clips-dir ./clips/ --output qa.json
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Optional dependency probes
# ---------------------------------------------------------------------------

_HAS_CV2 = False
try:
    import cv2
    import numpy as np

    _HAS_CV2 = True
except ImportError:
    pass

_HAS_DEEPFACE = False
try:
    from deepface import DeepFace  # type: ignore[import-untyped]

    _HAS_DEEPFACE = True
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Logging helper (always to stderr so stdout stays clean for piping)
# ---------------------------------------------------------------------------

def _log(msg: str) -> None:
    print(f"[qa-check] {msg}", file=sys.stderr, flush=True)


def _warn(msg: str) -> None:
    print(f"[qa-check] WARNING: {msg}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class FaceConsistencyResult:
    passed: bool = True
    min_similarity: float = 0.0
    mean_similarity: float = 0.0
    frames_checked: int = 0
    frames_with_face: int = 0
    skipped: bool = False
    skip_reason: str = ""


@dataclass
class VideoQualityResult:
    passed: bool = True
    resolution: str = ""
    fps: float = 0.0
    duration: float = 0.0
    mean_ssim: float = 0.0
    min_ssim: float = 0.0
    min_ssim_pair: str = ""
    issues: list[str] = field(default_factory=list)


@dataclass
class ColorConsistencyResult:
    passed: bool = True
    l_correlation: float = 0.0
    a_correlation: float = 0.0
    b_correlation: float = 0.0


@dataclass
class ClipResult:
    file: str = ""
    passed: bool = True
    overall_score: float = 0.0
    face_consistency: Optional[FaceConsistencyResult] = None
    video_quality: Optional[VideoQualityResult] = None
    color_consistency: Optional[ColorConsistencyResult] = None
    issues: list[str] = field(default_factory=list)


@dataclass
class Summary:
    total: int = 0
    passed: int = 0
    failed: int = 0
    failed_clips: list[str] = field(default_factory=list)


@dataclass
class QAReport:
    clips: list[ClipResult] = field(default_factory=list)
    summary: Summary = field(default_factory=Summary)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_ffprobe(clip_path: str, *args: str) -> Optional[str]:
    """Run ffprobe and return stdout, or None on failure."""
    cmd = ["ffprobe", "-v", "error", *args, clip_path]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return None
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def _extract_frames(clip_path: str, fps: float = 2.0) -> list[str]:
    """Extract frames at the given fps into a temp dir. Returns sorted frame paths."""
    tmpdir = tempfile.mkdtemp(prefix="qa_frames_")
    pattern = os.path.join(tmpdir, "frame_%05d.png")
    cmd = [
        "ffmpeg", "-v", "error", "-i", clip_path,
        "-vf", f"fps={fps}",
        "-q:v", "2", pattern,
    ]
    try:
        subprocess.run(cmd, capture_output=True, timeout=120, check=True)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return []

    frames = sorted(
        os.path.join(tmpdir, f)
        for f in os.listdir(tmpdir)
        if f.endswith(".png")
    )
    return frames


def _cleanup_frames(frame_paths: list[str]) -> None:
    """Remove extracted frame files and their parent temp directory."""
    if not frame_paths:
        return
    parent = os.path.dirname(frame_paths[0])
    for fp in frame_paths:
        try:
            os.remove(fp)
        except OSError:
            pass
    try:
        os.rmdir(parent)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Tier 1: Face Consistency
# ---------------------------------------------------------------------------

_FACE_SIMILARITY_THRESHOLD = 0.50


def check_face_consistency(
    clip_path: str,
    reference_face: str,
) -> FaceConsistencyResult:
    """Compare faces in clip frames against the reference image."""

    if not _HAS_DEEPFACE:
        _warn("DeepFace not installed — skipping face consistency check. "
              "Install with: pip install deepface tf-keras")
        return FaceConsistencyResult(
            skipped=True,
            skip_reason="DeepFace not installed",
        )

    frames = _extract_frames(clip_path, fps=2.0)
    if not frames:
        _warn(f"Could not extract frames from {clip_path} for face check")
        return FaceConsistencyResult(
            skipped=True,
            skip_reason="Frame extraction failed",
        )

    similarities: list[float] = []
    frames_checked = len(frames)

    for frame_path in frames:
        try:
            result = DeepFace.verify(
                img1_path=reference_face,
                img2_path=frame_path,
                model_name="ArcFace",
                distance_metric="cosine",
                enforce_detection=False,
                silent=True,
            )
            # DeepFace returns distance; convert to similarity
            # ArcFace uses cosine distance: similarity = 1 - distance
            distance = result.get("distance", 1.0)
            similarity = 1.0 - distance
            if result.get("facial_areas", {}).get("img2", {}).get("w", 0) > 0:
                similarities.append(similarity)
        except Exception:
            # Face not detected or comparison failed — skip this frame
            pass

    _cleanup_frames(frames)

    frames_with_face = len(similarities)
    if frames_with_face == 0:
        return FaceConsistencyResult(
            passed=True,  # No faces found isn't necessarily a failure
            frames_checked=frames_checked,
            frames_with_face=0,
        )

    min_sim = min(similarities)
    mean_sim = sum(similarities) / len(similarities)
    passed = min_sim >= _FACE_SIMILARITY_THRESHOLD

    return FaceConsistencyResult(
        passed=passed,
        min_similarity=round(min_sim, 4),
        mean_similarity=round(mean_sim, 4),
        frames_checked=frames_checked,
        frames_with_face=frames_with_face,
    )


# ---------------------------------------------------------------------------
# Tier 2: Video Quality
# ---------------------------------------------------------------------------

def _get_video_info(clip_path: str) -> dict:
    """Extract resolution, fps, duration via ffprobe."""
    info: dict = {"has_video": False, "width": 0, "height": 0, "fps": 0.0, "duration": 0.0}

    # Check for video stream
    stream_check = _run_ffprobe(
        clip_path,
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate,duration",
        "-of", "json",
    )
    if stream_check is None:
        return info

    try:
        data = json.loads(stream_check)
        streams = data.get("streams", [])
        if not streams:
            return info
        stream = streams[0]
        info["has_video"] = True
        info["width"] = int(stream.get("width", 0))
        info["height"] = int(stream.get("height", 0))

        # Parse frame rate (can be "30/1" or "30000/1001")
        rfr = stream.get("r_frame_rate", "0/1")
        if "/" in rfr:
            num, den = rfr.split("/")
            info["fps"] = float(num) / float(den) if float(den) != 0 else 0.0
        else:
            info["fps"] = float(rfr)
    except (json.JSONDecodeError, ValueError, KeyError):
        return info

    # Duration from format (more reliable than stream duration)
    dur_str = _run_ffprobe(
        clip_path,
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
    )
    if dur_str:
        try:
            info["duration"] = float(dur_str)
        except ValueError:
            pass

    return info


def _compute_ssim_between_frames(frame_a: str, frame_b: str) -> Optional[float]:
    """Compute SSIM between two frame images using OpenCV."""
    if not _HAS_CV2:
        return None

    img_a = cv2.imread(frame_a, cv2.IMREAD_GRAYSCALE)
    img_b = cv2.imread(frame_b, cv2.IMREAD_GRAYSCALE)
    if img_a is None or img_b is None:
        return None

    # Resize to match if dimensions differ
    if img_a.shape != img_b.shape:
        h = min(img_a.shape[0], img_b.shape[0])
        w = min(img_a.shape[1], img_b.shape[1])
        img_a = cv2.resize(img_a, (w, h))
        img_b = cv2.resize(img_b, (w, h))

    # SSIM computation (simplified — matches scikit-image default params)
    c1 = (0.01 * 255) ** 2
    c2 = (0.03 * 255) ** 2

    img_a = img_a.astype(np.float64)
    img_b = img_b.astype(np.float64)

    mu_a = cv2.GaussianBlur(img_a, (11, 11), 1.5)
    mu_b = cv2.GaussianBlur(img_b, (11, 11), 1.5)

    mu_a_sq = mu_a ** 2
    mu_b_sq = mu_b ** 2
    mu_ab = mu_a * mu_b

    sigma_a_sq = cv2.GaussianBlur(img_a ** 2, (11, 11), 1.5) - mu_a_sq
    sigma_b_sq = cv2.GaussianBlur(img_b ** 2, (11, 11), 1.5) - mu_b_sq
    sigma_ab = cv2.GaussianBlur(img_a * img_b, (11, 11), 1.5) - mu_ab

    numerator = (2 * mu_ab + c1) * (2 * sigma_ab + c2)
    denominator = (mu_a_sq + mu_b_sq + c1) * (sigma_a_sq + sigma_b_sq + c2)

    ssim_map = numerator / denominator
    return float(np.mean(ssim_map))


def check_video_quality(
    clip_path: str,
    expected_duration: Optional[float] = None,
) -> VideoQualityResult:
    """Verify basic video quality metrics and temporal consistency."""
    result = VideoQualityResult()
    issues: list[str] = []

    # -- Probe video info --
    info = _get_video_info(clip_path)

    if not info["has_video"]:
        issues.append("No video stream found")
        result.passed = False
        result.issues = issues
        return result

    w, h = info["width"], info["height"]
    result.resolution = f"{w}x{h}"
    result.fps = round(info["fps"], 2)
    result.duration = round(info["duration"], 2)

    # Resolution check (>= 720p means shortest side >= 720)
    min_dim = min(w, h)
    if min_dim < 720:
        issues.append(f"Resolution below 720p ({result.resolution})")

    # FPS check
    if info["fps"] < 24:
        issues.append(f"Frame rate below 24fps ({result.fps})")

    # Duration check
    if expected_duration is not None and info["duration"] > 0:
        tolerance = 1.0  # 1 second tolerance
        if abs(info["duration"] - expected_duration) > tolerance:
            issues.append(
                f"Duration {result.duration}s outside expected "
                f"{expected_duration}s (±{tolerance}s)"
            )

    # -- Temporal consistency via SSIM --
    if _HAS_CV2:
        frames = _extract_frames(clip_path, fps=2.0)  # every 0.5s
        if len(frames) >= 2:
            ssim_scores: list[tuple[float, str]] = []
            for i in range(len(frames) - 1):
                ssim = _compute_ssim_between_frames(frames[i], frames[i + 1])
                if ssim is not None:
                    pair_label = f"frame_{i:05d}-frame_{i + 1:05d}"
                    ssim_scores.append((ssim, pair_label))

            _cleanup_frames(frames)

            if ssim_scores:
                values = [s[0] for s in ssim_scores]
                result.mean_ssim = round(sum(values) / len(values), 4)
                min_idx = values.index(min(values))
                result.min_ssim = round(values[min_idx], 4)
                result.min_ssim_pair = ssim_scores[min_idx][1]

                if result.min_ssim < 0.65:
                    issues.append(
                        f"Jarring temporal jump detected (SSIM {result.min_ssim} "
                        f"at {result.min_ssim_pair})"
                    )
        else:
            _cleanup_frames(frames)
    else:
        _warn("OpenCV not installed — skipping SSIM temporal consistency check. "
              "Install with: pip install opencv-python-headless numpy")

    if issues:
        result.passed = False
    result.issues = issues
    return result


# ---------------------------------------------------------------------------
# Tier 3: Color Consistency (multi-clip)
# ---------------------------------------------------------------------------

_COLOR_CORRELATION_THRESHOLD = 0.7


def _extract_first_frame(clip_path: str) -> Optional[str]:
    """Extract just the first frame of a clip."""
    tmpdir = tempfile.mkdtemp(prefix="qa_color_")
    out_path = os.path.join(tmpdir, "first_frame.png")
    cmd = [
        "ffmpeg", "-v", "error", "-i", clip_path,
        "-frames:v", "1", "-q:v", "2", out_path,
    ]
    try:
        subprocess.run(cmd, capture_output=True, timeout=30, check=True)
        if os.path.exists(out_path):
            return out_path
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def _lab_histogram(image_path: str) -> Optional[tuple]:
    """Compute L, A, B channel histograms for an image."""
    if not _HAS_CV2:
        return None

    img = cv2.imread(image_path)
    if img is None:
        return None

    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    hist_l = cv2.calcHist([lab], [0], None, [256], [0, 256]).flatten()
    hist_a = cv2.calcHist([lab], [1], None, [256], [0, 256]).flatten()
    hist_b = cv2.calcHist([lab], [2], None, [256], [0, 256]).flatten()

    # Normalize
    hist_l = hist_l / (hist_l.sum() + 1e-10)
    hist_a = hist_a / (hist_a.sum() + 1e-10)
    hist_b = hist_b / (hist_b.sum() + 1e-10)

    return hist_l, hist_a, hist_b


def _histogram_correlation(h1, h2) -> float:
    """Compute correlation between two histograms using OpenCV."""
    return float(cv2.compareHist(
        h1.astype(np.float32),
        h2.astype(np.float32),
        cv2.HISTCMP_CORREL,
    ))


def check_color_consistency(
    clip_paths: list[str],
) -> dict[str, ColorConsistencyResult]:
    """Compare LAB color histograms across clips against the group average."""
    if not _HAS_CV2:
        _warn("OpenCV not installed — skipping color consistency check")
        return {}

    if len(clip_paths) < 2:
        return {}

    # Extract first frame and compute LAB histograms for each clip
    clip_hists: list[tuple[str, tuple]] = []
    temp_frames: list[str] = []

    for cp in clip_paths:
        frame = _extract_first_frame(cp)
        if frame is None:
            continue
        temp_frames.append(frame)
        hists = _lab_histogram(frame)
        if hists is not None:
            clip_hists.append((cp, hists))

    if len(clip_hists) < 2:
        # Cleanup temp frames
        for tf in temp_frames:
            try:
                os.remove(tf)
                os.rmdir(os.path.dirname(tf))
            except OSError:
                pass
        return {}

    # Compute average histogram
    n = len(clip_hists)
    avg_l = sum(h[0] for _, h in clip_hists) / n
    avg_a = sum(h[1] for _, h in clip_hists) / n
    avg_b = sum(h[2] for _, h in clip_hists) / n

    results: dict[str, ColorConsistencyResult] = {}
    for cp, (hist_l, hist_a, hist_b) in clip_hists:
        l_corr = _histogram_correlation(hist_l, avg_l)
        a_corr = _histogram_correlation(hist_a, avg_a)
        b_corr = _histogram_correlation(hist_b, avg_b)

        passed = (
            a_corr >= _COLOR_CORRELATION_THRESHOLD
            and b_corr >= _COLOR_CORRELATION_THRESHOLD
        )

        results[cp] = ColorConsistencyResult(
            passed=passed,
            l_correlation=round(l_corr, 4),
            a_correlation=round(a_corr, 4),
            b_correlation=round(b_corr, 4),
        )

    # Cleanup
    for tf in temp_frames:
        try:
            os.remove(tf)
            os.rmdir(os.path.dirname(tf))
        except OSError:
            pass

    return results


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def _compute_overall_score(clip: ClipResult) -> float:
    """Weighted score from 0.0 to 1.0 across all tiers."""
    scores: list[tuple[float, float]] = []  # (score, weight)

    # Face consistency: weight 0.3 (only if not skipped)
    if clip.face_consistency and not clip.face_consistency.skipped:
        fc = clip.face_consistency
        if fc.frames_with_face > 0:
            scores.append((fc.mean_similarity, 0.3))
        else:
            scores.append((1.0, 0.3))  # No faces expected = fine

    # Video quality: weight 0.5
    if clip.video_quality:
        vq = clip.video_quality
        vq_score = 1.0
        if not vq.passed:
            # Deduct per issue
            vq_score = max(0.0, 1.0 - len(vq.issues) * 0.25)
        # Blend in SSIM if available
        if vq.mean_ssim > 0:
            vq_score = vq_score * 0.6 + vq.mean_ssim * 0.4
        scores.append((vq_score, 0.5))

    # Color consistency: weight 0.2
    if clip.color_consistency:
        cc = clip.color_consistency
        avg_corr = (cc.l_correlation + cc.a_correlation + cc.b_correlation) / 3
        scores.append((avg_corr, 0.2))

    if not scores:
        return 0.0

    total_weight = sum(w for _, w in scores)
    if total_weight == 0:
        return 0.0

    weighted = sum(s * w for s, w in scores) / total_weight
    return round(max(0.0, min(1.0, weighted)), 2)


def run_qa(
    clip_paths: list[str],
    reference_face: Optional[str] = None,
    expected_duration: Optional[float] = None,
) -> QAReport:
    """Run all QA tiers on the provided clips."""
    report = QAReport()
    total = len(clip_paths)

    _log(f"Starting QA on {total} clip(s)")

    # -- Tier 1 & 2: per-clip checks --
    clip_results: list[ClipResult] = []
    for i, cp in enumerate(clip_paths, 1):
        name = os.path.basename(cp)
        _log(f"[{i}/{total}] Checking {name}")
        cr = ClipResult(file=name)

        # Tier 1: Face consistency
        if reference_face:
            _log(f"  Tier 1: Face consistency")
            cr.face_consistency = check_face_consistency(cp, reference_face)
            if cr.face_consistency.skipped:
                _log(f"    Skipped: {cr.face_consistency.skip_reason}")
            elif not cr.face_consistency.passed:
                cr.issues.append(
                    f"Face similarity below threshold "
                    f"(min={cr.face_consistency.min_similarity})"
                )

        # Tier 2: Video quality
        _log(f"  Tier 2: Video quality")
        cr.video_quality = check_video_quality(cp, expected_duration)
        if not cr.video_quality.passed:
            cr.issues.extend(cr.video_quality.issues)

        clip_results.append(cr)

    # -- Tier 3: Color consistency (multi-clip) --
    if len(clip_paths) >= 2:
        _log("Tier 3: Color consistency across clips")
        color_results = check_color_consistency(clip_paths)
        for cr in clip_results:
            full_path = next(
                (p for p in clip_paths if os.path.basename(p) == cr.file),
                None,
            )
            if full_path and full_path in color_results:
                cr.color_consistency = color_results[full_path]
                if not cr.color_consistency.passed:
                    cr.issues.append(
                        f"Color drift detected "
                        f"(A={cr.color_consistency.a_correlation}, "
                        f"B={cr.color_consistency.b_correlation})"
                    )

    # -- Compute scores and pass/fail --
    for cr in clip_results:
        cr.overall_score = _compute_overall_score(cr)
        cr.passed = len(cr.issues) == 0

    # -- Summary --
    passed_count = sum(1 for cr in clip_results if cr.passed)
    failed_clips = [cr.file for cr in clip_results if not cr.passed]

    report.clips = clip_results
    report.summary = Summary(
        total=total,
        passed=passed_count,
        failed=total - passed_count,
        failed_clips=failed_clips,
    )

    _log(f"Done: {passed_count}/{total} passed")
    return report


# ---------------------------------------------------------------------------
# Serialization helper
# ---------------------------------------------------------------------------

def _serialize_report(report: QAReport) -> dict:
    """Convert report to a clean JSON-serializable dict, stripping None values
    and internal fields like skipped/skip_reason when not relevant."""
    raw = asdict(report)

    def _clean(obj):
        if isinstance(obj, dict):
            cleaned = {}
            for k, v in obj.items():
                if v is None:
                    continue
                # Strip internal fields from face_consistency when not skipped
                if k in ("skipped", "skip_reason"):
                    if not v:
                        continue
                cleaned[k] = _clean(v)
            return cleaned
        if isinstance(obj, list):
            return [_clean(item) for item in obj]
        if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
            return 0.0
        return obj

    return _clean(raw)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="QA check for AI-generated music video clips",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--clip",
        type=str,
        help="Path to a single clip to check",
    )
    group.add_argument(
        "--clips-dir",
        type=str,
        help="Directory containing clips to check",
    )
    parser.add_argument(
        "--reference-face",
        type=str,
        default=None,
        help="Reference face image for consistency checking",
    )
    parser.add_argument(
        "--expected-duration",
        type=float,
        default=None,
        help="Expected clip duration in seconds (tolerance ±1s)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="qa-results.json",
        help="Output JSON file path (default: qa-results.json)",
    )

    args = parser.parse_args()

    # Resolve clip paths
    clip_paths: list[str] = []
    if args.clip:
        cp = os.path.abspath(args.clip)
        if not os.path.isfile(cp):
            _log(f"Error: clip not found: {cp}")
            sys.exit(1)
        clip_paths = [cp]
    elif args.clips_dir:
        clips_dir = os.path.abspath(args.clips_dir)
        if not os.path.isdir(clips_dir):
            _log(f"Error: directory not found: {clips_dir}")
            sys.exit(1)
        video_exts = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
        clip_paths = sorted(
            os.path.join(clips_dir, f)
            for f in os.listdir(clips_dir)
            if os.path.splitext(f)[1].lower() in video_exts
        )
        if not clip_paths:
            _log(f"No video files found in {clips_dir}")
            sys.exit(1)

    # Validate reference face
    ref_face: Optional[str] = None
    if args.reference_face:
        ref_face = os.path.abspath(args.reference_face)
        if not os.path.isfile(ref_face):
            _log(f"Error: reference face not found: {ref_face}")
            sys.exit(1)

    # Verify ffprobe is available
    try:
        subprocess.run(
            ["ffprobe", "-version"],
            capture_output=True,
            timeout=10,
        )
    except FileNotFoundError:
        _log("Error: ffprobe not found. Install ffmpeg: brew install ffmpeg")
        sys.exit(1)

    # Run QA
    report = run_qa(
        clip_paths=clip_paths,
        reference_face=ref_face,
        expected_duration=args.expected_duration,
    )

    # Write output
    output_path = os.path.abspath(args.output)
    serialized = _serialize_report(report)
    with open(output_path, "w") as f:
        json.dump(serialized, f, indent=2)
    _log(f"Results written to {output_path}")

    # Exit code: 0 if all passed, 1 if any failed
    if report.summary.failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
