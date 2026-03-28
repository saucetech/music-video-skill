#!/usr/bin/env python3
"""
Generate ASS karaoke subtitles from aligned lyrics JSON.

Produces an ASS file with \\kf tags for smooth left-to-right word color sweep.
Style: Bottom-center, gold highlight on white text, gradient backdrop.

Usage:
    python3 generate-ass.py --aligned aligned-lyrics.json --output karaoke.ass
    python3 generate-ass.py --aligned aligned-lyrics.json --output karaoke.ass --style neon
"""

import argparse
import json
import math


# Style presets
STYLES = {
    "karaoke": {
        "name": "Karaoke",
        "font": "Arial Black",
        "size": 72,
        "primary": "&H00FFFFFF",       # White
        "secondary": "&H0000D7FF",     # Gold (#FFD700 in BGR)
        "outline": "&H00000000",       # Black
        "back": "&H80000000",          # Semi-transparent black
        "bold": -1,
        "outline_width": 3,
        "shadow": 2,
        "alignment": 2,               # Bottom-center
        "margin_v": 60,
        "spacing": 2,
    },
    "neon": {
        "name": "NeonKaraoke",
        "font": "Helvetica Neue",
        "size": 68,
        "primary": "&H00FFFFFF",
        "secondary": "&H0000FF00",     # Green
        "outline": "&H00FF8800",       # Orange outline
        "back": "&H40000000",
        "bold": -1,
        "outline_width": 4,
        "shadow": 3,
        "alignment": 2,
        "margin_v": 80,
        "spacing": 3,
    },
    "minimal": {
        "name": "MinimalKaraoke",
        "font": "Inter",
        "size": 64,
        "primary": "&H80FFFFFF",       # Semi-transparent white
        "secondary": "&H00FFFFFF",     # Full white (highlight)
        "outline": "&H00000000",
        "back": "&H60000000",
        "bold": 0,
        "outline_width": 2,
        "shadow": 1,
        "alignment": 2,
        "margin_v": 50,
        "spacing": 4,
    },
}


def time_to_ass(seconds):
    """Convert seconds to ASS timestamp format H:MM:SS.cc"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    centiseconds = int((secs - int(secs)) * 100)
    return f"{hours}:{minutes:02d}:{int(secs):02d}.{centiseconds:02d}"


def generate_ass(aligned_data, style_name="karaoke"):
    """Generate complete ASS file content from aligned lyrics data."""
    style = STYLES.get(style_name, STYLES["karaoke"])
    lines_data = aligned_data.get("lines", [])

    # Header
    ass = f"""[Script Info]
Title: Karaoke Subtitles
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: {style['name']},{style['font']},{style['size']},{style['primary']},{style['secondary']},{style['outline']},{style['back']},{style['bold']},0,0,0,100,100,{style['spacing']},0,1,{style['outline_width']},{style['shadow']},{style['alignment']},40,40,{style['margin_v']},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    # Generate dialogue lines
    for line in lines_data:
        words = line.get("words", [])
        if not words:
            continue

        line_start = time_to_ass(line["start"])
        line_end = time_to_ass(line["end"])

        # Build karaoke tags for each word
        karaoke_text = ""
        for i, word in enumerate(words):
            # Duration in centiseconds
            duration_cs = max(1, int((word["end"] - word["start"]) * 100))
            text = word["word"]

            # Add space before word (except first)
            if i > 0:
                text = " " + text

            karaoke_text += f"{{\\kf{duration_cs}}}{text}"

        ass += f"Dialogue: 0,{line_start},{line_end},{style['name']},,0,0,0,,{karaoke_text}\n"

    return ass


def main():
    parser = argparse.ArgumentParser(description="Generate ASS karaoke subtitles")
    parser.add_argument("--aligned", required=True, help="Path to aligned-lyrics.json")
    parser.add_argument("--output", required=True, help="Output ASS file path")
    parser.add_argument("--style", default="karaoke", choices=list(STYLES.keys()),
                        help="Style preset (default: karaoke)")
    args = parser.parse_args()

    with open(args.aligned) as f:
        aligned_data = json.load(f)

    ass_content = generate_ass(aligned_data, args.style)

    with open(args.output, "w") as f:
        f.write(ass_content)

    line_count = len(aligned_data.get("lines", []))
    word_count = aligned_data.get("metadata", {}).get("total_words", 0)
    print(f"Generated ASS file: {args.output}")
    print(f"  Style: {args.style}")
    print(f"  Lines: {line_count}")
    print(f"  Words: {word_count}")
    print(f"  Preview: mpv --sub-file={args.output} <video-file>")


if __name__ == "__main__":
    main()
