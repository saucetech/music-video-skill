#!/usr/bin/env python3
"""
Align user-provided lyrics with Whisper transcription output.

Uses Whisper for timing accuracy (when words start/end) and user lyrics
for word accuracy (correct spelling, punctuation, line breaks).

Usage:
    python3 align-lyrics.py --whisper whisper-raw.json --lyrics lyrics.txt --output aligned.json
    echo "lyrics text" | python3 align-lyrics.py --whisper whisper-raw.json --output aligned.json
"""

import argparse
import json
import sys
import re
from difflib import SequenceMatcher


def normalize(text):
    """Normalize text for comparison: lowercase, strip punctuation."""
    return re.sub(r"[^\w\s]", "", text.lower()).strip()


def split_lyrics_to_lines(lyrics_text):
    """Split lyrics text into lines, preserving line structure."""
    lines = []
    for line in lyrics_text.strip().split("\n"):
        line = line.strip()
        if line:
            words = line.split()
            lines.append({"text": line, "words": words})
    return lines


def extract_whisper_words(whisper_data):
    """Extract flat word list with timing from Whisper output."""
    words = []
    for segment in whisper_data.get("segments", []):
        for w in segment.get("words", []):
            words.append({
                "word": w["word"].strip(),
                "start": w["start"],
                "end": w["end"],
                "probability": w.get("probability", 0),
            })
    return words


def align_words(user_words, whisper_words):
    """
    Align user-provided words with Whisper words using sequence matching.
    Returns user words with Whisper timing attached.
    """
    # Normalize both word lists for matching
    user_norm = [normalize(w) for w in user_words]
    whisper_norm = [normalize(w["word"]) for w in whisper_words]

    # Use SequenceMatcher for fuzzy alignment
    matcher = SequenceMatcher(None, user_norm, whisper_norm)
    aligned = []

    # Track which whisper words have been consumed
    whisper_idx = 0

    for op, u_start, u_end, w_start, w_end in matcher.get_opcodes():
        if op == "equal":
            for i, j in zip(range(u_start, u_end), range(w_start, w_end)):
                aligned.append({
                    "word": user_words[i],  # Use user spelling
                    "start": whisper_words[j]["start"],
                    "end": whisper_words[j]["end"],
                    "confidence": whisper_words[j]["probability"],
                    "match_type": "exact",
                })
        elif op == "replace":
            # Words differ — use user text, Whisper timing
            u_count = u_end - u_start
            w_count = w_end - w_start

            if u_count == w_count:
                # 1:1 replacement — direct timing mapping
                for i, j in zip(range(u_start, u_end), range(w_start, w_end)):
                    aligned.append({
                        "word": user_words[i],
                        "start": whisper_words[j]["start"],
                        "end": whisper_words[j]["end"],
                        "confidence": whisper_words[j]["probability"],
                        "match_type": "fuzzy",
                    })
            else:
                # Different counts — distribute timing evenly
                total_start = whisper_words[w_start]["start"] if w_count > 0 else (aligned[-1]["end"] if aligned else 0)
                total_end = whisper_words[w_end - 1]["end"] if w_count > 0 else total_start + 0.5
                duration = total_end - total_start
                per_word = duration / u_count if u_count > 0 else 0

                for i, idx in enumerate(range(u_start, u_end)):
                    aligned.append({
                        "word": user_words[idx],
                        "start": round(total_start + i * per_word, 3),
                        "end": round(total_start + (i + 1) * per_word, 3),
                        "confidence": 0.5,
                        "match_type": "interpolated",
                    })
        elif op == "insert":
            # Words in user lyrics not in Whisper — interpolate timing
            if aligned:
                prev_end = aligned[-1]["end"]
            else:
                prev_end = 0
            # Look ahead for next Whisper timing
            next_start = whisper_words[w_start]["start"] if w_start < len(whisper_words) else prev_end + 0.5
            duration = next_start - prev_end
            count = u_end - u_start
            per_word = duration / count if count > 0 else 0.3

            for i, idx in enumerate(range(u_start, u_end)):
                aligned.append({
                    "word": user_words[idx],
                    "start": round(prev_end + i * per_word, 3),
                    "end": round(prev_end + (i + 1) * per_word, 3),
                    "confidence": 0.3,
                    "match_type": "inserted",
                })
        elif op == "delete":
            # Words in Whisper not in user lyrics — skip them
            pass

    return aligned


def build_aligned_output(lyrics_lines, aligned_words):
    """Build the final aligned output with line structure preserved."""
    output = []
    word_idx = 0

    for line_idx, line in enumerate(lyrics_lines):
        line_words = []
        for word_text in line["words"]:
            if word_idx < len(aligned_words):
                entry = aligned_words[word_idx].copy()
                entry["line_index"] = line_idx
                entry["is_line_break"] = False
                line_words.append(entry)
                word_idx += 1

        # Mark last word of each line
        if line_words:
            line_words[-1]["is_line_break"] = True

        output.extend(line_words)

    return output


def main():
    parser = argparse.ArgumentParser(description="Align lyrics with Whisper timestamps")
    parser.add_argument("--whisper", required=True, help="Path to whisper-raw.json")
    parser.add_argument("--lyrics", help="Path to lyrics text file (or pipe via stdin)")
    parser.add_argument("--output", required=True, help="Output path for aligned-lyrics.json")
    args = parser.parse_args()

    # Load Whisper data
    with open(args.whisper) as f:
        whisper_data = json.load(f)

    # Load lyrics
    if args.lyrics:
        with open(args.lyrics) as f:
            lyrics_text = f.read()
    elif not sys.stdin.isatty():
        lyrics_text = sys.stdin.read()
    else:
        print("ERROR: Provide lyrics via --lyrics file or stdin", file=sys.stderr)
        sys.exit(1)

    # Process
    lyrics_lines = split_lyrics_to_lines(lyrics_text)
    whisper_words = extract_whisper_words(whisper_data)

    # Flatten user words for alignment
    all_user_words = []
    for line in lyrics_lines:
        all_user_words.extend(line["words"])

    print(f"User lyrics: {len(all_user_words)} words across {len(lyrics_lines)} lines")
    print(f"Whisper transcript: {len(whisper_words)} words")

    # Align
    aligned_words = align_words(all_user_words, whisper_words)

    # Build output with line structure
    output = build_aligned_output(lyrics_lines, aligned_words)

    # Stats
    match_types = {}
    for w in output:
        t = w.get("match_type", "unknown")
        match_types[t] = match_types.get(t, 0) + 1

    print(f"Alignment results: {match_types}")

    # Also include line-level data for convenience
    lines_output = []
    current_line = {"words": [], "line_index": 0}
    for w in output:
        if current_line["line_index"] != w["line_index"]:
            if current_line["words"]:
                current_line["text"] = " ".join(ww["word"] for ww in current_line["words"])
                current_line["start"] = current_line["words"][0]["start"]
                current_line["end"] = current_line["words"][-1]["end"]
                lines_output.append(current_line)
            current_line = {"words": [], "line_index": w["line_index"]}
        current_line["words"].append(w)

    # Don't forget last line
    if current_line["words"]:
        current_line["text"] = " ".join(ww["word"] for ww in current_line["words"])
        current_line["start"] = current_line["words"][0]["start"]
        current_line["end"] = current_line["words"][-1]["end"]
        lines_output.append(current_line)

    final_output = {
        "words": output,
        "lines": lines_output,
        "metadata": {
            "total_words": len(output),
            "total_lines": len(lines_output),
            "match_stats": match_types,
            "language": whisper_data.get("language", "en"),
        },
    }

    with open(args.output, "w") as f:
        json.dump(final_output, f, indent=2)

    print(f"Aligned lyrics saved to: {args.output}")


if __name__ == "__main__":
    main()
