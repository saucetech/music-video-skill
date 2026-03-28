#!/usr/bin/env python3
"""
Render kinetic typography frames using Puppeteer (via Node.js).

Takes aligned lyrics JSON and HTML templates, renders each frame at 30fps
as transparent PNG images for compositing over video.

Usage:
    python3 render-kinetic-frames.py \
        --aligned aligned-lyrics.json \
        --templates ~/.claude/skills/music-video/templates/kinetic/ \
        --output ./kinetic-frames/ \
        --fps 30 \
        --template bold-centered
"""

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile


def generate_puppeteer_script(template_path, lyrics_data, output_dir, fps, start_time, end_time):
    """Generate a Node.js script that uses Puppeteer to render frames."""

    lyrics_json = json.dumps(lyrics_data)

    script = f"""
const puppeteer = require('puppeteer');
const path = require('path');
const fs = require('fs');

(async () => {{
    const browser = await puppeteer.launch({{
        headless: 'new',
        args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-gpu']
    }});

    const page = await browser.newPage();
    await page.setViewport({{ width: 1920, height: 1080 }});

    // Load the template
    const templatePath = '{template_path}';
    await page.goto('file://' + templatePath, {{ waitUntil: 'networkidle0' }});

    // Inject lyrics data
    const lyricsData = {lyrics_json};
    await page.evaluate((data) => {{
        window.LYRICS_DATA = data;
    }}, lyricsData);

    const fps = {fps};
    const startTime = {start_time};
    const endTime = {end_time};
    const totalFrames = Math.ceil((endTime - startTime) * fps);
    const outputDir = '{output_dir}';

    // Ensure output directory exists
    if (!fs.existsSync(outputDir)) {{
        fs.mkdirSync(outputDir, {{ recursive: true }});
    }}

    console.log(`Rendering ${{totalFrames}} frames at ${{fps}}fps...`);

    // Create a transparent PNG for blank frames
    const blankFramePath = path.join(outputDir, '_blank.png');

    for (let i = 0; i < totalFrames; i++) {{
        const currentTime = startTime + (i / fps);
        const frameNum = String(i + 1).padStart(4, '0');
        const framePath = path.join(outputDir, `${{frameNum}}.png`);

        // Update frame time and trigger render
        await page.evaluate((time) => {{
            window.FRAME_TIME = time;
            if (typeof window.renderFrame === 'function') {{
                window.renderFrame(time);
            }}
        }}, currentTime);

        // Small delay for CSS transitions to apply
        await new Promise(r => setTimeout(r, 10));

        // Screenshot with transparency
        await page.screenshot({{
            path: framePath,
            omitBackground: true,
            type: 'png'
        }});

        // Progress logging every 30 frames (1 second)
        if ((i + 1) % 30 === 0 || i === totalFrames - 1) {{
            const pct = Math.round(((i + 1) / totalFrames) * 100);
            const timeStr = currentTime.toFixed(2);
            console.log(`  Frame ${{i + 1}}/${{totalFrames}} (${{pct}}%) @ ${{timeStr}}s`);
        }}
    }}

    await browser.close();
    console.log(`Done! ${{totalFrames}} frames saved to ${{outputDir}}`);
}})();
"""
    return script


def main():
    parser = argparse.ArgumentParser(description="Render kinetic typography frames")
    parser.add_argument("--aligned", required=True, help="Path to aligned-lyrics.json")
    parser.add_argument("--templates", required=True, help="Path to kinetic templates directory")
    parser.add_argument("--output", required=True, help="Output directory for PNG frames")
    parser.add_argument("--fps", type=int, default=30, help="Frames per second (default: 30)")
    parser.add_argument("--template", default="bold-centered",
                        help="Template name (default: bold-centered)")
    parser.add_argument("--style-primary", default="#FFFFFF", help="Primary text color")
    parser.add_argument("--style-accent", default="#FFD700", help="Accent/highlight color")
    parser.add_argument("--style-font-size", default="90px", help="Base font size")
    args = parser.parse_args()

    # Load aligned lyrics
    with open(args.aligned) as f:
        aligned_data = json.load(f)

    # Build lyrics data for the template
    lyrics_data = {
        "lines": aligned_data.get("lines", []),
        "style": {
            "primaryColor": args.style_primary,
            "accentColor": args.style_accent,
            "fontSize": args.style_font_size,
        },
    }

    # Determine time range
    all_words = aligned_data.get("words", [])
    if not all_words:
        print("ERROR: No words found in aligned lyrics", file=sys.stderr)
        sys.exit(1)

    start_time = max(0, all_words[0]["start"] - 1.0)  # 1s buffer before first word
    end_time = all_words[-1]["end"] + 1.0  # 1s buffer after last word

    # Resolve template path
    template_file = f"{args.template}.html"
    template_path = os.path.join(os.path.abspath(args.templates), template_file)
    if not os.path.exists(template_path):
        print(f"ERROR: Template not found: {template_path}", file=sys.stderr)
        print(f"Available templates:", file=sys.stderr)
        for f_name in os.listdir(args.templates):
            if f_name.endswith(".html"):
                print(f"  - {f_name.replace('.html', '')}", file=sys.stderr)
        sys.exit(1)

    output_dir = os.path.abspath(args.output)
    total_frames = math.ceil((end_time - start_time) * args.fps)
    duration = end_time - start_time

    print(f"Kinetic Typography Frame Renderer")
    print(f"  Template: {args.template}")
    print(f"  Time range: {start_time:.2f}s - {end_time:.2f}s ({duration:.2f}s)")
    print(f"  FPS: {args.fps}")
    print(f"  Total frames: {total_frames}")
    print(f"  Output: {output_dir}")
    print()

    # Check for Puppeteer
    try:
        subprocess.run(["npx", "puppeteer", "--version"],
                       capture_output=True, check=True, timeout=10)
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        print("WARNING: Puppeteer not found. Attempting to install...", file=sys.stderr)
        subprocess.run(["npm", "install", "-g", "puppeteer"], check=True)

    # Generate and run Puppeteer script
    script = generate_puppeteer_script(
        template_path, lyrics_data, output_dir, args.fps, start_time, end_time
    )

    # Write script to temp file and execute
    with tempfile.NamedTemporaryFile(mode="w", suffix=".js", delete=False) as f:
        f.write(script)
        script_path = f.name

    try:
        result = subprocess.run(
            ["node", script_path],
            capture_output=False,
            check=True,
        )
    finally:
        os.unlink(script_path)

    # Verify output
    frame_files = sorted(f for f in os.listdir(output_dir)
                         if f.endswith(".png") and not f.startswith("_"))
    print(f"\nRendered {len(frame_files)} frames to {output_dir}")

    if len(frame_files) != total_frames:
        print(f"WARNING: Expected {total_frames} frames, got {len(frame_files)}", file=sys.stderr)


if __name__ == "__main__":
    main()
