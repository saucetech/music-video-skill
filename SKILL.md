---
name: music-video
description: >
  Produce full AI music videos and karaoke videos from an audio file + lyrics.
  8-phase pipeline: audio intelligence (beat detection, structure, stems),
  creative storyboarding (genre-aware, beat-snapped), SEEDREAM image generation,
  Kling 3.0 video generation with character consistency (element binding),
  automated QA (face check, artifact detection, auto-retry), beat-synced
  assembly with reactive effects, karaoke or kinetic typography captions
  (9 templates), and multi-format export (YouTube, TikTok clips, Shorts,
  thumbnail). Triggers include: "music video", "make a music video",
  "lyric video", "karaoke video", "video for my song", "animate my song",
  "generate music video", "create a video from this song", "music video
  from audio", "visualize this track", "make visuals for my music".
  Do NOT use for: short-form ad videos (use /ad-scriptwriter), talking-head
  videos (use /video-production), B-roll generation (use /broll), caption
  overlays only (use /captions).
user-invocable: true
model: claude-opus-4-6
status: active
metadata: {"openclaw": {"requires": {"env": ["FAL_KEY"], "bins": ["ffmpeg", "ffprobe", "python3"], "anyBins": ["npx"]}, "primaryEnv": "FAL_KEY", "emoji": "🎬"}}
---

# Music Video

Produce cinematic AI music videos from an audio file and lyrics. Beat-synced visuals, consistent characters, genre-aware storytelling, automated quality control.

## Critical Rules

1. **Beat-sync is non-negotiable** — every scene cut lands on a detected beat. The audio intelligence phase completes before any visual work begins.
2. **Motion-only prompts for i2v** — describe ONLY motion and camera (15-40 words). The character and scene exist in the image. Redescribing appearance causes drift.
3. **Never fabricate lyrics** — user's text for words, Whisper for timing.
4. **QA every clip before assembly** — no clip enters the timeline without passing QA.
5. **Present cost estimate before spending money** — get user confirmation before calling fal.ai generation endpoints.

## Prerequisites

```bash
which ffmpeg && which ffprobe && which python3
python3 -c "import whisper"    # pip install openai-whisper
python3 -c "import librosa"    # pip install librosa
python3 -c "import demucs"     # pip install demucs
echo $FAL_KEY                   # fal.ai API key
# Optional: pip install deepface (QA), npm install -g puppeteer (kinetic mode)
```

## Inputs

| Input | Required | Description |
|-------|----------|-------------|
| Audio file | Yes | MP3, WAV, or FLAC |
| Lyrics | Yes | Full lyrics (pasted or file path) |
| Mode | Yes | Karaoke (word-by-word highlight) or Music Video (kinetic typography) |
| Genre | Yes | pop, hip-hop, rock, EDM, R&B, indie, country |
| Video model | No | Kling 3.0 Pro / Standard (default) / 2.6 Pro |
| Character ref | No | 1-4 photos (frontal required). Enables element binding in Kling 3.0. |
| Concept | No | Creative direction hint |
| Auto mode | No | Skip approval pauses. Default: pause for review. |

**FPS by genre:** Pop/EDM = 30fps. Hip-hop/Rock/R&B/Indie/Country = 24fps (cinematic).

---

## Phase 1: Audio Intelligence

All later phases depend on this. Run it first, entirely local, no cost.

**Step 1.1:** Validate audio with `ffprobe`. Extract duration. Reject if not MP3/WAV/FLAC.

**Step 1.2:** Stem separation — isolate vocals and instrumental for better analysis:
```bash
python3 {baseDir}/scripts/audio-intelligence.py --audio <file> --output-dir <dir>
```
This runs demucs (htdemucs, two-stems), then librosa for beats/onsets/energy/structure. If demucs unavailable, uses original audio. Falls back gracefully.

**Step 1.3:** Transcribe with Whisper large-v3 (falls back to medium → base):
```bash
bash {baseDir}/scripts/transcribe.sh <audio-file> <output-dir> [--vocals-stem <stems/vocals.wav>]
```

**Step 1.4:** Align user lyrics with Whisper timing:
```bash
python3 {baseDir}/scripts/align-lyrics.py --whisper <dir>/whisper-raw.json --lyrics <file> --output <dir>/aligned-lyrics.json
```

If >30% "interpolated" matches → warn user lyrics may not match audio.

**Output files:** `aligned-lyrics.json`, `beat-map.json`, `song-structure.json`, `stems/`

---

## Phase 2: Creative Direction

Read `{baseDir}/references/creative-direction.md` for genre tables, composition rules, visual metaphors, and color arcs.

**Step 2.1: Analyze lyrics** — extract themes, narrative arc, key imagery, emotional progression, quotable lines for TikTok clips.

**Step 2.2: Design the visual through-line** — fill ALL five before writing any scenes:

1. **Visual motif** — one recurring symbol that transforms across the arc (appears in 4+ scenes)
2. **Narrative through-line** — a simple A→B feeling transformation ("trapped → free")
3. **Color temperature arc** — how palette shifts per section (cool verse → warm chorus → contrasting bridge)
4. **Bridge departure** — what makes the bridge visually different (new location, color, perspective)
5. **Visual bookend** — final scene rhymes with first scene, but transformed

**Step 2.3: Generate storyboard** — plan scenes beat-snapped to `beat-map.json`.

- Minimum clip duration: 5 seconds (Kling constraint)
- Scene density: Intro/Outro 1-2 scenes, Verse 2-4, Chorus 2-4, Bridge 1-2
- For chorus energy: use faster camera movement WITHIN clips, not faster cuts between clips
- Rotate shot scale: wide → medium → close-up → wide (avoid monotony)
- Include composition, lens, and camera system tokens in every prompt (see reference)

Scene format:
```
### Scene {N}: {start} - {end}
Section: | Lyrics: | Mood: | Visual motif: | Visual: | Motion prompt: | Camera: | Transition:
```

**Step 2.4: Cost estimate + approval** — calculate cost, present to user, get confirmation.

---

## Phase 3: Storyboard Image Generation

Read `{baseDir}/references/fal-api-patterns.md` for exact API calls.

**Step 3.1:** Upload character reference images to fal.ai CDN (if provided).

**Step 3.2:** Generate all storyboard frames with SEEDREAM v4.5 ($0.04/image).
- Same seed across all scenes for style consistency
- Same style prefix prepended to every prompt
- Identical character descriptions when character appears

**Step 3.3:** Optional upscaling (SeedVR2 or CCSR) before video generation.

**Step 3.4: Storyboard face QA** — if character reference provided, run DeepFace ArcFace comparison on EVERY storyboard image BEFORE video generation. Threshold: 0.45 similarity. Regenerate mismatches up to 3x with different seeds. This prevents paying for Kling video on frames with the wrong character. Read `{baseDir}/references/qa-pipeline.md` for details.

**Step 3.5:** If not auto mode, generate `storyboard-review.html` for user approval.

---

## Phase 4: Video Generation

Read `{baseDir}/references/fal-api-patterns.md` for exact API calls per model.

**Model selection:**
- Kling 3.0 Pro ($0.112/s) — best quality, element binding
- Kling 3.0 Standard ($0.084/s) — good quality, element binding
- Kling 2.6 Pro ($0.07/s) — budget, NO element binding

**If Kling 3.0 + character reference:** use `elements` parameter with frontal + side reference images. Reference as `@Element1` in prompt. This reduces face drift from ~50% to <10%.

**If Kling 2.6 or no character ref:** standard i2v, consistency relies on storyboard image quality only.

**Submit all scenes in parallel** via `fal_client.submit()`. Collect results as they complete.

**Verify each clip** with `ffprobe` — correct aspect ratio, duration, has video stream.

---

## Phase 5: QA Layer

Read `{baseDir}/references/qa-pipeline.md` for thresholds and code.

Run on every video clip before assembly:

1. **Face consistency** (if character ref) — DeepFace ArcFace at 2fps, threshold 0.50
2. **Temporal consistency** — SSIM between consecutive frames, threshold 0.65
3. **Color consistency** — LAB histogram correlation across all clips, threshold 0.7

```bash
python3 {baseDir}/scripts/qa-check.py --clips-dir ./clips/ --reference-face ref.png --output qa-results.json
```

**Auto-retry:** new seed → new seed → simplified prompt → flag for human review.

---

## Phase 6: Beat-Synced Assembly

Read `{baseDir}/references/beat-sync.md` for transition tables and effect details.

```bash
bash {baseDir}/scripts/assemble.sh \
  --clips ./clips/ --audio song.mp3 \
  --mode karaoke|music-video \
  --captions ./karaoke.ass|./kinetic-frames/ \
  --beat-map ./beat-map.json --song-structure ./song-structure.json \
  --output ./final.mp4 [--beat-effects]
```

This script handles: clip normalization (genre FPS + color normalization) → beat-snapped xfade chain (transition type by section energy) → beat-reactive effects (zoom/flash/saturation, BPM-scaled) → caption overlay → audio mux → verification.

Beat-reactive effects: on by default for EDM/hip-hop, off for ballads. Intensity scales inversely with BPM (see reference).

---

## Phase 7: Caption Rendering

Read `{baseDir}/references/kinetic-templates.md` for template details and genre mapping.

**Karaoke mode:**
```bash
python3 {baseDir}/scripts/generate-ass.py --aligned aligned-lyrics.json --output karaoke.ass --style karaoke
```
Presets: `karaoke` (gold on white), `neon` (green glow), `minimal` (clean fade-in).

**Kinetic typography mode:**
```bash
python3 {baseDir}/scripts/render-kinetic-frames.py \
  --aligned aligned-lyrics.json --templates "{baseDir}/templates/kinetic/" \
  --template "01-text-slam" --output ./kinetic-frames/ --fps 30
```
9 templates. Genre defaults: hip-hop→Text Slam, EDM→Beat Pulse, R&B→Gradient Wipe, etc.

---

## Phase 8: Export

Read `{baseDir}/references/export-formats.md` for encoding specs.

**YouTube** (always): 1920x1080, H.264 CRF 18, AAC 320k, `+faststart`

**YouTube chapters** (always): auto-generated from `song-structure.json` → `youtube-chapters.txt`

**TikTok clips** (if requested):
```bash
python3 {baseDir}/scripts/vertical-crop.py \
  --video final.mp4 --song-structure song-structure.json --beat-map beat-map.json \
  --output-dir ./tiktok-clips/ --num-clips 5 [--smart-crop]
```

**Thumbnail** (always): best storyboard image → 1280x720 PNG

---

## Working Directory

```
<song-name>-music-video/
├── aligned-lyrics.json, beat-map.json, song-structure.json
├── creative-brief.md
├── stems/ (vocals.wav, no_vocals.wav)
├── storyboard/ (scene-01.png ... scene-NN.png, storyboard-review.html)
├── clips/ (scene-01.mp4 ... scene-NN.mp4, qa-results.json)
├── karaoke.ass | kinetic-frames/
├── <song-name>-music-video.mp4 (final)
├── youtube-chapters.txt, thumbnail.png
└── tiktok-clip-{1-5}.mp4
```

## Error Handling

**fal.ai:** 422→fix input, 429→SDK auto-retries, 500→auto-retried, COMPLETED+error→check content moderation

**Whisper:** large-v3 OOM → medium → base. Poor alignment → ask user to verify lyrics.

**ffmpeg:** DTS errors → add `-fflags +genpts`. Memory with 20+ clips → batch in groups of 5.

**No beats detected:** Set beats every 2s, warn user. Slow tempo (<60 BPM) → cut every 4-8 beats.

## Quality Standards

1. Every scene cut within 100ms of a detected beat
2. Face similarity >0.50 (ArcFace) if character reference provided
3. Every lyric line has visible caption during its timestamp
4. No black frames, frozen frames, or audio gaps at boundaries
5. Final duration within 1s of original audio
6. H.264 CRF 18, AAC 320k, movflags +faststart

## Critical Rules (Recap)

1. **Beat-sync is non-negotiable** — cuts land on beats.
2. **Motion-only prompts for i2v** — never redescribe the character.
3. **Never fabricate lyrics** — user text for words, Whisper for timing.
4. **QA every clip** — nothing unreviewed enters the timeline.
5. **Cost estimate first** — confirm before spending.
