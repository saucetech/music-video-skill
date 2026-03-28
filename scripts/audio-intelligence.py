#!/usr/bin/env python3
"""
Audio intelligence pipeline for music video production.

Analyzes an audio file to produce three outputs:
  1. Stem separation (vocals + instrumental via demucs)
  2. Beat map (BPM, beat/onset timestamps, energy curve via librosa)
  3. Song structure (intro/verse/chorus/bridge/outro via chroma clustering)

Usage:
    python3 scripts/audio-intelligence.py \\
        --audio song.mp3 \\
        --output-dir ./working/ \\
        [--skip-stems] \\
        [--device cpu|cuda]
"""

import argparse
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

import numpy as np

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("audio-intelligence")


# ---------------------------------------------------------------------------
# Stem separation
# ---------------------------------------------------------------------------

def run_demucs(
    audio_path: str,
    output_dir: str,
    device: str = "cpu",
) -> Optional[dict]:
    """
    Run demucs htdemucs two-stem separation.

    Returns dict with paths to vocals.wav and no_vocals.wav,
    or None if demucs is not available.
    """
    stems_dir = os.path.join(output_dir, "stems")
    os.makedirs(stems_dir, exist_ok=True)

    # Check if demucs is installed
    try:
        subprocess.run(
            ["demucs", "--help"],
            capture_output=True,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        log.warning("demucs not found — skipping stem separation")
        return None

    log.info("Running demucs htdemucs (two-stem mode)...")

    # demucs outputs to <out>/<model>/<track>/ by default
    # We use --two-stems vocals to get vocals + no_vocals
    cmd = [
        "demucs",
        "--two-stems", "vocals",
        "-n", "htdemucs",
        "-d", device,
        "-o", stems_dir,
        audio_path,
    ]

    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        log.error("demucs failed: %s", e.stderr)
        return None

    # Locate output files — demucs writes to <stems_dir>/htdemucs/<track_name>/
    track_name = Path(audio_path).stem
    demucs_out = os.path.join(stems_dir, "htdemucs", track_name)

    vocals_path = os.path.join(demucs_out, "vocals.wav")
    no_vocals_path = os.path.join(demucs_out, "no_vocals.wav")

    if not os.path.exists(vocals_path) or not os.path.exists(no_vocals_path):
        log.error(
            "Expected stem files not found in %s — found: %s",
            demucs_out,
            os.listdir(demucs_out) if os.path.isdir(demucs_out) else "dir missing",
        )
        return None

    log.info("Stems saved: %s, %s", vocals_path, no_vocals_path)
    return {"vocals": vocals_path, "no_vocals": no_vocals_path}


# ---------------------------------------------------------------------------
# Beat detection
# ---------------------------------------------------------------------------

def detect_beats(audio_path: str, sr: int = 22050) -> dict:
    """
    Analyze rhythm: BPM, beat timestamps, onset timestamps, energy curve.

    Uses librosa on the instrumental stem (or original audio).
    Returns a dict ready for JSON serialization.
    """
    import librosa

    log.info("Loading audio for beat detection: %s", audio_path)
    y, sr = librosa.load(audio_path, sr=sr, mono=True)
    duration = librosa.get_duration(y=y, sr=sr)
    log.info("Audio loaded — duration: %.1fs, sr: %d", duration, sr)

    # BPM + Beat frames (single call — beat_track returns both)
    tempo_result, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
    bpm = float(tempo_result[0]) if hasattr(tempo_result, "__len__") else float(tempo_result)
    log.info("Detected BPM: %.1f", bpm)
    beats = librosa.frames_to_time(beat_frames, sr=sr).tolist()
    log.info("Detected %d beats", len(beats))

    # Onset frames → timestamps
    onset_frames = librosa.onset.onset_detect(y=y, sr=sr)
    onsets = librosa.frames_to_time(onset_frames, sr=sr).tolist()
    log.info("Detected %d onsets", len(onsets))

    # Energy curve (RMS), normalized 0-1
    rms = librosa.feature.rms(y=y)[0]
    rms_max = rms.max()
    if rms_max > 0:
        rms_norm = rms / rms_max
    else:
        rms_norm = rms

    hop_length = 512  # librosa default
    energy = []
    for i, val in enumerate(rms_norm):
        t = librosa.frames_to_time(i, sr=sr, hop_length=hop_length)
        energy.append({"time": round(float(t), 3), "value": round(float(val), 4)})

    log.info("Energy curve: %d samples", len(energy))

    return {
        "bpm": round(bpm, 1),
        "beats": [round(b, 3) for b in beats],
        "onsets": [round(o, 3) for o in onsets],
        "energy": energy,
    }


# ---------------------------------------------------------------------------
# Song structure detection
# ---------------------------------------------------------------------------

def detect_structure(audio_path: str, beat_map: dict, sr: int = 22050) -> dict:
    """
    Detect song sections using chroma features + agglomerative clustering.

    Labels sections by energy profile and position, then snaps boundaries
    to the nearest beat from the beat map.
    """
    import librosa
    from scipy.cluster.hierarchy import fcluster, linkage
    from scipy.spatial.distance import pdist

    log.info("Analyzing song structure: %s", audio_path)
    y, sr = librosa.load(audio_path, sr=sr, mono=True)
    duration = librosa.get_duration(y=y, sr=sr)

    # Chroma features — use CQT for better pitch resolution
    hop_length = 512
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=hop_length)

    # Build self-similarity matrix from chroma
    # Segment chroma into fixed-size blocks for clustering
    block_duration = 4.0  # seconds per block
    frames_per_block = int(block_duration * sr / hop_length)
    n_frames = chroma.shape[1]
    n_blocks = max(1, n_frames // frames_per_block)

    # Average chroma per block
    block_features = []
    block_times = []
    for i in range(n_blocks):
        start_frame = i * frames_per_block
        end_frame = min((i + 1) * frames_per_block, n_frames)
        block_chroma = chroma[:, start_frame:end_frame].mean(axis=1)
        block_features.append(block_chroma)
        t = librosa.frames_to_time(start_frame, sr=sr, hop_length=hop_length)
        block_times.append(float(t))

    if len(block_features) < 2:
        # Too short for meaningful structure — return single section
        return {"sections": [{"start": 0.0, "end": round(duration, 2), "label": "verse", "energy_avg": 0.5}]}

    block_features = np.array(block_features)

    # Agglomerative clustering
    # Choose number of clusters based on song length
    max_clusters = min(len(block_features), max(3, int(duration / 20)))
    distances = pdist(block_features, metric="cosine")
    linkage_matrix = linkage(distances, method="ward")
    labels = fcluster(linkage_matrix, t=max_clusters, criterion="maxclust")

    # Compute per-block energy from the beat map energy curve
    energy_data = beat_map.get("energy", [])
    block_energies = _compute_block_energies(block_times, block_duration, energy_data)

    # Merge consecutive blocks with the same cluster label into sections
    raw_sections = _merge_blocks_to_sections(
        labels, block_times, block_duration, block_energies, duration,
    )

    # Label sections based on energy and position
    labeled = _label_sections(raw_sections)

    # Snap boundaries to nearest beat
    beats = beat_map.get("beats", [])
    snapped = _snap_to_beats(labeled, beats, duration)

    log.info("Detected %d sections", len(snapped))
    for s in snapped:
        log.info(
            "  %s: %.1f–%.1f (energy %.2f)",
            s["label"], s["start"], s["end"], s["energy_avg"],
        )

    return {"sections": snapped}


def _compute_block_energies(
    block_times: list[float],
    block_duration: float,
    energy_data: list[dict],
) -> list[float]:
    """Compute average energy for each block from the energy curve."""
    energies = []
    for bt in block_times:
        block_end = bt + block_duration
        vals = [
            e["value"] for e in energy_data
            if bt <= e["time"] < block_end
        ]
        energies.append(sum(vals) / len(vals) if vals else 0.0)
    return energies


def _merge_blocks_to_sections(
    labels: np.ndarray,
    block_times: list[float],
    block_duration: float,
    block_energies: list[float],
    total_duration: float,
) -> list[dict]:
    """Merge consecutive same-cluster blocks into raw sections."""
    sections = []
    current_label = labels[0]
    current_start = block_times[0]
    current_energies = [block_energies[0]]

    for i in range(1, len(labels)):
        if labels[i] != current_label:
            # Close the current section
            section_end = block_times[i]
            sections.append({
                "start": round(current_start, 3),
                "end": round(section_end, 3),
                "cluster": int(current_label),
                "energy_avg": round(sum(current_energies) / len(current_energies), 3),
            })
            current_label = labels[i]
            current_start = block_times[i]
            current_energies = [block_energies[i]]
        else:
            current_energies.append(block_energies[i])

    # Final section extends to end of audio
    sections.append({
        "start": round(current_start, 3),
        "end": round(total_duration, 3),
        "cluster": int(current_label),
        "energy_avg": round(sum(current_energies) / len(current_energies), 3),
    })

    return sections


def _label_sections(sections: list[dict]) -> list[dict]:
    """
    Assign musical labels (intro, verse, chorus, bridge, outro) based on
    energy profile, repetition (cluster frequency), and position.
    """
    if not sections:
        return []

    # Count how often each cluster appears — repeating clusters are likely
    # verse or chorus; unique clusters are likely bridge
    from collections import Counter
    cluster_counts = Counter(s["cluster"] for s in sections)

    # Determine energy thresholds
    all_energies = [s["energy_avg"] for s in sections]
    energy_median = float(np.median(all_energies))
    energy_p75 = float(np.percentile(all_energies, 75))

    labeled = []
    for i, section in enumerate(sections):
        is_first = (i == 0)
        is_last = (i == len(sections) - 1)
        e = section["energy_avg"]
        cluster = section["cluster"]
        count = cluster_counts[cluster]
        section_duration = section["end"] - section["start"]

        # Labeling heuristics
        if is_first and e < energy_median and section_duration < 20:
            label = "intro"
        elif is_last and e < energy_median:
            label = "outro"
        elif e >= energy_p75 and count >= 2:
            label = "chorus"
        elif count == 1 and not is_first and not is_last:
            label = "bridge"
        else:
            label = "verse"

        labeled.append({
            "start": section["start"],
            "end": section["end"],
            "label": label,
            "energy_avg": section["energy_avg"],
        })

    return labeled


def _snap_to_beats(
    sections: list[dict],
    beats: list[float],
    duration: float,
) -> list[dict]:
    """Snap section boundaries to the nearest beat timestamp."""
    if not beats:
        return sections

    beats_arr = np.array(beats)

    def nearest_beat(t: float) -> float:
        if len(beats_arr) == 0:
            return t
        idx = np.argmin(np.abs(beats_arr - t))
        return round(float(beats_arr[idx]), 3)

    snapped = []
    for i, section in enumerate(sections):
        start = nearest_beat(section["start"]) if i > 0 else 0.0
        end = nearest_beat(section["end"]) if i < len(sections) - 1 else round(duration, 3)
        snapped.append({
            "start": start,
            "end": end,
            "label": section["label"],
            "energy_avg": section["energy_avg"],
        })

    # Fix any overlaps or gaps from snapping — each section starts where
    # the previous one ended
    for i in range(1, len(snapped)):
        snapped[i]["start"] = snapped[i - 1]["end"]

    # Drop zero-length sections that snapping may have created
    snapped = [s for s in snapped if s["end"] > s["start"]]

    return snapped


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------

def write_json(data: dict, path: str) -> None:
    """Write dict to a JSON file with pretty formatting."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    log.info("Wrote %s", path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audio intelligence pipeline — beat map + song structure from audio",
    )
    parser.add_argument(
        "--audio",
        required=True,
        help="Path to input audio file (mp3, wav, flac, etc.)",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory to write output files (beat-map.json, song-structure.json, stems/)",
    )
    parser.add_argument(
        "--skip-stems",
        action="store_true",
        default=False,
        help="Skip demucs stem separation (use original audio for everything)",
    )
    parser.add_argument(
        "--device",
        choices=["cpu", "cuda"],
        default="cpu",
        help="Device for demucs inference (default: cpu)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    audio_path = os.path.abspath(args.audio)
    output_dir = os.path.abspath(args.output_dir)

    if not os.path.isfile(audio_path):
        log.error("Audio file not found: %s", audio_path)
        sys.exit(1)

    os.makedirs(output_dir, exist_ok=True)
    log.info("Audio: %s", audio_path)
    log.info("Output: %s", output_dir)

    # ── Step 1: Stem separation ──────────────────────────────────────────
    instrumental_path = audio_path  # fallback: use original audio
    stems = None

    if args.skip_stems:
        log.info("Stem separation skipped (--skip-stems)")
    else:
        stems = run_demucs(audio_path, output_dir, device=args.device)
        if stems:
            instrumental_path = stems["no_vocals"]
        else:
            log.warning("Falling back to original audio for beat detection")

    # ── Step 2: Beat detection ───────────────────────────────────────────
    beat_map = detect_beats(instrumental_path)
    beat_map_path = os.path.join(output_dir, "beat-map.json")
    write_json(beat_map, beat_map_path)

    # ── Step 3: Song structure ───────────────────────────────────────────
    structure = detect_structure(instrumental_path, beat_map)
    structure_path = os.path.join(output_dir, "song-structure.json")
    write_json(structure, structure_path)

    # ── Summary ──────────────────────────────────────────────────────────
    log.info("Done — outputs:")
    log.info("  Beat map:       %s", beat_map_path)
    log.info("  Song structure: %s", structure_path)
    if stems:
        log.info("  Vocals:         %s", stems["vocals"])
        log.info("  Instrumental:   %s", stems["no_vocals"])


if __name__ == "__main__":
    main()
