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
---

# Music Video

Produce cinematic AI music videos from an audio file and lyrics. Beat-synced visuals, consistent characters, genre-aware storytelling, automated quality control.

## Critical Rules

1. **Beat-sync is non-negotiable** — every scene cut MUST land on a detected beat. Never place cuts between beats. The audio intelligence phase must complete before any visual work begins.
2. **Motion-only prompts for i2v** — when generating video from storyboard images, describe ONLY motion and camera movement (15-40 words). The character and scene already exist in the image. Redescribing appearance causes the model to reinterpret and drift.
3. **Never fabricate lyrics** — use only the user-provided lyrics text. If Whisper disagrees with the user's lyrics, trust the user's text for words and Whisper for timing.
4. **QA every video clip before assembly** — no clip enters the final timeline without passing the QA check. A bad clip ruins the entire video.
5. **Present cost estimate before spending money** — show the user total estimated cost (images + video clips) and get confirmation before calling any fal.ai generation endpoints.

## Prerequisites

Check before starting. Stop and tell the user what's missing.

```bash
# Required
which ffmpeg          # video assembly
which ffprobe         # media inspection
which python3         # scripts
python3 -c "import whisper"     # transcription (pip install openai-whisper)
echo $FAL_KEY                    # fal.ai API key

# Required Python packages
python3 -c "import librosa"     # beat detection (pip install librosa)
python3 -c "import demucs"      # stem separation (pip install demucs)

# Optional (for QA)
python3 -c "import deepface"    # face consistency (pip install deepface)

# Optional (for kinetic typography mode)
which npx && npx puppeteer --version  # frame rendering
```

## Inputs

Collect from the user before starting:

| Input | Required | Description |
|-------|----------|-------------|
| Audio file | Yes | Path to MP3, WAV, or FLAC |
| Lyrics | Yes | Full lyrics text (pasted or file path) |
| Mode | Yes | Ask: "Karaoke (word-by-word highlight) or Music Video (kinetic typography)?" |
| Genre | Yes | Ask: "What genre? (pop, hip-hop, rock, EDM, R&B, indie, country, other)" |
| Video model | No | Default: Kling 3.0 Standard. Options: Kling 3.0 Pro, Kling 3.0 Standard, Kling 2.6 Pro |
| Character ref | No | 1-4 photos of the character/artist (frontal required, side/profile optional). Enables element binding in Kling 3.0. |
| Concept | No | Creative direction hint (e.g., "neo-noir cyberpunk cityscape") |
| Auto mode | No | Ask: "Auto-generate everything, or pause for storyboard approval?" Default: pause. |

---

## Phase 1: Audio Intelligence

This phase extracts everything we need from the audio before any visual work begins. All subsequent phases depend on this output.

### Step 1.1: Validate Audio

```bash
ffprobe -v quiet -print_format json -show_format -show_streams "<audio-file>"
```

Extract: duration, format, sample rate. Reject if not MP3/WAV/FLAC.

### Step 1.2: Stem Separation (demucs)

Separate the audio into stems for better analysis:

```bash
python3 -m demucs --two-stems vocals -n htdemucs "<audio-file>" -o "<output-dir>/stems"
```

This produces `vocals.wav` and `no_vocals.wav`. The isolated vocals improve Whisper accuracy. The instrumental improves beat detection accuracy.

If demucs fails (not enough RAM, etc.) → skip and use the original audio for both transcription and beat detection. Log warning but continue.

### Step 1.3: Transcribe with Whisper

```bash
python3 -c "
import whisper, json
model = whisper.load_model('large-v3')
result = model.transcribe('<vocals-stem-or-original>', word_timestamps=True)
with open('<output-dir>/whisper-raw.json', 'w') as f:
    json.dump(result, f, indent=2)
"
```

If `large-v3` fails (OOM) → fall back to `medium`, then `base`. Log which model was used.

### Step 1.4: Align Lyrics

Cross-reference Whisper timing with user-provided lyrics for word accuracy:

```bash
python3 scripts/align-lyrics.py \
  --whisper "<output-dir>/whisper-raw.json" \
  --lyrics "<lyrics-file>" \
  --output "<output-dir>/aligned-lyrics.json"
```

Output: `{"words": [{word, start, end, line_index, is_line_break, confidence, match_type}, ...], "lines": [{text, start, end, words: [...]}, ...], "metadata": {...}}`

If alignment has many "interpolated" matches (>30%) → warn user that lyrics may not match audio well. Ask them to verify.

### Step 1.5: Beat Detection

Run on the instrumental stem (no_vocals.wav) for cleaner beat detection:

```python
import librosa, json

y, sr = librosa.load("<output-dir>/stems/no_vocals.wav")

# Beats
tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
beat_times = librosa.frames_to_time(beat_frames, sr=sr).tolist()

# Onsets (sub-beat hits — snares, kicks, hats)
onset_frames = librosa.onset.onset_detect(y=y, sr=sr)
onset_times = librosa.frames_to_time(onset_frames, sr=sr).tolist()

# Energy curve (RMS, normalized 0-1)
rms = librosa.feature.rms(y=y)[0]
rms_times = librosa.frames_to_time(range(len(rms)), sr=sr).tolist()
rms_norm = (rms / rms.max()).tolist()

output = {
    "bpm": float(tempo),
    "beats": [round(b, 3) for b in beat_times],
    "onsets": [round(o, 3) for o in onset_times],
    "energy": [{"time": round(t, 3), "value": round(v, 4)} for t, v in zip(rms_times, rms_norm)],
}

with open("<output-dir>/beat-map.json", "w") as f:
    json.dump(output, f, indent=2)
```

If demucs was skipped, run beat detection on the original audio instead.

### Step 1.6: Song Structure Detection

Use librosa's structural segmentation to detect verse/chorus/bridge boundaries:

```python
import librosa
import numpy as np

y, sr = librosa.load("<audio-file>")

# Compute chroma features
chroma = librosa.feature.chroma_cqt(y=y, sr=sr)

# Recurrence matrix for self-similarity
R = librosa.segment.recurrence_matrix(chroma, mode="affinity", sym=True)

# Laplacian segmentation
bound_frames = librosa.segment.agglomerative(chroma, k=8)
bound_times = librosa.frames_to_time(bound_frames, sr=sr).tolist()
```

Combine with the aligned lyrics line breaks and beat map to produce:

```json
// <output-dir>/song-structure.json
{
  "sections": [
    {"start": 0.0, "end": 15.2, "label": "intro", "energy_avg": 0.3},
    {"start": 15.2, "end": 45.8, "label": "verse", "energy_avg": 0.5},
    {"start": 45.8, "end": 75.1, "label": "chorus", "energy_avg": 0.85},
    ...
  ]
}
```

Label assignment heuristic:
- First section with no/few lyrics → "intro"
- Sections with moderate energy → "verse"
- Sections with high energy that repeat → "chorus"
- Sections that appear once with contrasting energy → "bridge"
- Last section with falling energy → "outro"

This is approximate. If the user provides section labels, trust those instead.

### Phase 1 Output Files

| File | Content |
|------|---------|
| `aligned-lyrics.json` | Word-level timestamps + line structure |
| `beat-map.json` | BPM, beat times, onset times, energy curve |
| `song-structure.json` | Section boundaries with labels + energy |
| `stems/vocals.wav` | Isolated vocals (if demucs ran) |
| `stems/no_vocals.wav` | Instrumental (if demucs ran) |

---

## Phase 2: Creative Direction

### Step 2.1: Analyze Lyrics

Using the aligned lyrics and song structure, extract:

- **Themes**: 3-5 core themes
- **Narrative arc**: How the story/emotion progresses
- **Key imagery**: Concrete visual elements mentioned in lyrics
- **Emotional progression**: Map each section to an emotional state
- **Quotable lines**: Lines that would make great TikTok clips (high-impact, standalone)

### Step 2.2: Genre Visual Language

Apply genre-specific visual conventions:

| Genre | Palette | Lighting | Locations | Camera Style |
|-------|---------|----------|-----------|-------------|
| **Pop** | Bright, saturated, clean | Even, polished | Fashion-forward, varied | Steady, smooth tracking |
| **Hip-hop** | High-contrast, neon accents | Hard shadows, rim light | Urban, nightlife, studio | Low angles, slow-mo |
| **Rock** | Desaturated, earthy | Harsh, practical | Warehouses, stages, outdoors | Handheld feel, fast cuts |
| **EDM** | Neon, hyper-saturated | Strobes, colored gels | Clubs, festivals, abstract | Dynamic, zooms, glitch |
| **R&B** | Warm amber, rich tones | Soft backlight, warm | Intimate interiors, golden hour | Shallow DOF, close-ups |
| **Indie** | Muted, faded, vintage | Natural, overcast | Nature, small towns, bedrooms | Static, contemplative |
| **Country** | Golden, warm earth | Golden hour, natural | Open fields, porches, roads | Wide establishing shots |

### Step 2.3: Generate Storyboard (Beat-Snapped)

For each section in `song-structure.json`, plan scenes. Scene boundaries MUST land on beats from `beat-map.json`.

**Scene density by section type:**
- Intro/outro: 1-2 scenes (long holds, 8-16 beats each)
- Verse: 2-4 scenes (4-8 beats each, narrative, medium shots)
- Chorus: 3-6 scenes (2-4 beats each, faster cuts, performance/spectacle)
- Bridge: 1-2 scenes (visual departure, new palette or location)

**Shot variety cycle** — rotate through these to avoid monotony:
```
Wide establishing → Medium subject → Close-up detail →
Wide environment → Medium action → Close-up emotion →
(repeat with variation, intensifying toward chorus peaks)
```

For each scene, produce:

```markdown
### Scene {N}: {start_time} - {end_time}
**Section**: {verse/chorus/bridge/intro/outro}
**Lyrics**: "{lyrics for this section}"
**Mood**: {emotional quality}
**Visual**: {detailed SEEDREAM prompt — subject + action + setting + style + lighting + camera + color}
**Motion prompt**: {15-40 word Kling prompt — motion and camera ONLY}
**Camera**: {shot type + angle}
**Transition to next**: {cut type — hard cut, dissolve, fade, etc.}
```

Save to `<output-dir>/creative-brief.md`.

### Step 2.4: Storyboard Approval

If NOT in auto mode:
1. Present the creative brief to the user
2. Show total scene count and cost estimate
3. Ask for approval or changes
4. Iterate until approved

If in auto mode: skip approval, proceed.

**Cost estimate formula:**

```
image_cost = num_scenes * $0.04 (SEEDREAM)
video_cost = num_scenes * clip_duration_avg * model_cost_per_sec
total = image_cost + video_cost
```

| Model | Cost/sec |
|-------|----------|
| Kling 3.0 Pro | $0.112 |
| Kling 3.0 Standard | $0.084 |
| Kling 2.6 Pro | $0.07 |

---

## Phase 3: Storyboard Image Generation

### Step 3.1: Upload Character Reference (if provided)

```python
import fal_client

# Upload each reference image to fal.ai CDN
frontal_url = fal_client.upload_file("character-front.png")
side_urls = [fal_client.upload_file(f) for f in ["char-3quarter.png", "char-profile.png"]]
```

### Step 3.2: Generate Scene Images

For each scene in the creative brief, generate with SEEDREAM v4.5:

```python
result = fal_client.subscribe(
    "fal-ai/bytedance/seedream/v4.5/text-to-image",
    arguments={
        "prompt": scene["visual_prompt"],
        "negative_prompt": "blurry, low quality, distorted, watermark, text, logo, extra fingers, deformed hands",
        "image_size": "landscape_16_9",
        "num_images": 1,
        "seed": CONSISTENT_SEED,
    },
)
```

**Consistency strategy:**
- Use the same `seed` for all scenes
- Prepend every prompt with the same style prefix: `"cinematic film still, {genre_style_tokens}, {color_palette_description}, 8K, ultra detailed, "`
- If a character appears, use identical character descriptions across all prompts
- Keep lighting descriptors consistent within song sections

Save images to `<output-dir>/storyboard/scene-{NN}.png`.

### Step 3.3: Optional Upscaling

If user requests upscaling before video generation:

```python
# SeedVR2 — $0.001/megapixel, best for AI art
result = fal_client.subscribe(
    "fal-ai/seedvr/upscale/image",
    arguments={
        "image_url": scene_image_url,
        "scale_factor": 2,  # 1920x1080 → 3840x2160
    },
)

# CCSR — FREE alternative
result = fal_client.subscribe(
    "fal-ai/ccsr",
    arguments={
        "image_url": scene_image_url,
        "scale": 2,
    },
)
```

### Step 3.4: Storyboard Review

If NOT in auto mode, generate `storyboard-review.html` using the template at `templates/storyboard.html`. Open for user to approve/reject each scene. Regenerate rejected scenes with adjusted prompts.

---

## Phase 4: Video Generation

### Step 4.1: Generate Video Clips

For each approved storyboard frame, generate a video clip.

**If Kling 3.0 (Pro or Standard) AND character reference provided:**

```python
result = fal_client.subscribe(
    "fal-ai/kling-video/v3/pro/image-to-video",  # or v3/standard
    arguments={
        "prompt": scene["motion_prompt"],  # motion + camera ONLY
        "start_image_url": scene_image_url,
        "elements": [{
            "frontal_image_url": frontal_url,
            "reference_image_urls": side_urls,
        }],
        "duration": str(scene["duration_sec"]),
        "negative_prompt": "face morphing, identity change, deformed face, flickering, blurry",
        "cfg_scale": 0.5,
    },
)
```

Reference the character in the prompt as `@Element1`:
```
"@Element1 walks forward confidently, slow dolly push-in, warm cinematic lighting"
```

**If Kling 3.0 WITHOUT character reference:**

Same call but omit the `elements` parameter.

**If Kling 2.6:**

```python
result = fal_client.subscribe(
    "fal-ai/kling-video/v2.6/pro/image-to-video",
    arguments={
        "prompt": scene["motion_prompt"],
        "image_url": scene_image_url,
        "duration": "5",  # or "10"
        "aspect_ratio": "16:9",
    },
)
```

Note: Kling 2.6 does NOT support element binding. Character consistency relies entirely on the storyboard image quality.

**Submit all scenes in parallel** using `fal_client.submit()`, then collect results:

```python
handles = []
for scene in scenes:
    handle = fal_client.submit(endpoint, arguments=scene_args)
    handles.append((scene, handle))

for scene, handle in handles:
    result = handle.get()
    download_clip(result["video"]["url"], f"<output-dir>/clips/scene-{scene['number']:02d}.mp4")
```

### Step 4.2: Verify Each Clip

```bash
ffprobe -v quiet -print_format json -show_format -show_streams "<clip>"
```

Check: has video stream, correct aspect ratio, duration within expected range.

---

## Phase 5: QA Layer

Run QA on every generated video clip. Clips that fail are regenerated.

### Step 5.1: Face Consistency Check (if character reference provided)

```python
from deepface import DeepFace
import cv2

def check_face_consistency(clip_path, reference_face_path, threshold=0.50):
    """Extract frames, compare faces to reference. Return pass/fail + scores."""
    cap = cv2.VideoCapture(clip_path)
    fps = cap.get(cv2.CAP_PROP_FPS)
    frame_interval = max(1, int(fps / 2))  # check ~2 frames per second

    scores = []
    frame_idx = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if frame_idx % frame_interval == 0:
            try:
                result = DeepFace.verify(
                    frame, reference_face_path,
                    model_name="ArcFace",
                    distance_metric="cosine",
                    enforce_detection=False,
                )
                scores.append(1.0 - result["distance"])  # convert distance to similarity
            except:
                pass  # face not detected in this frame
        frame_idx += 1
    cap.release()

    if not scores:
        return {"passed": True, "reason": "no_faces_detected", "scores": []}

    min_score = min(scores)
    mean_score = sum(scores) / len(scores)
    return {
        "passed": min_score >= threshold,
        "min_similarity": min_score,
        "mean_similarity": mean_score,
        "frames_checked": len(scores),
    }
```

If DeepFace is not installed → skip this check, log warning.

### Step 5.2: Visual Artifact Check (Gemini 2.5 Flash)

For clips that have characters or complex scenes, send 3-5 evenly spaced frames to Gemini for artifact detection:

```
Analyze these AI-generated video frames for quality issues.
Check for:
1. Extra or missing fingers, twisted joints, duplicated limbs
2. Face deformation, melting features, asymmetric eyes
3. Objects morphing between frames
4. Unnatural motion or physics violations
5. Text or watermarks that shouldn't be there

For each issue found, describe it briefly.
Score overall quality 1-5 (5 = no issues, 1 = severe artifacts).
Return JSON: {"score": N, "issues": ["description", ...]}
```

If Gemini is not available or user opts out → skip this check.

### Step 5.3: Auto-Retry on Failure

```
If QA fails:
  → Attempt 1: Same prompt, new seed (different random seed)
  → Attempt 2: Same prompt, another new seed
  → Attempt 3: Simplified prompt (remove complex motion, keep it simple)
  → If still failing → flag for user review with the best attempt so far
     Show: "Scene {N} failed QA after 3 attempts. Best result: [score]. Use anyway or provide feedback?"
```

---

## Phase 6: Beat-Synced Assembly

### Step 6.1: Normalize All Clips

```bash
for clip in clips/scene-*.mp4; do
  ffmpeg -y -i "$clip" \
    -vf "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,fps=30,format=yuv420p" \
    -c:v libx264 -crf 18 -preset fast -an \
    "normalized/$(basename "$clip")"
done
```

### Step 6.2: Color Normalization

Apply baseline normalization to every clip for consistent brightness/contrast:

```bash
ffmpeg -i clip.mp4 -vf "normalize=blackpt=black:whitept=white:smoothing=20" normalized.mp4
```

If user provided a `.cube` LUT file, also apply it:
```bash
ffmpeg -i clip.mp4 -vf "normalize=...,lut3d=file=user.cube" normalized.mp4
```

### Step 6.3: Beat-Snapped Crossfade Chain

Scene cut points are snapped to the nearest beat from `beat-map.json`. Transition types are selected by energy delta:

| Energy Transition | Cut Type | Duration |
|-------------------|----------|----------|
| Low → Low (verse → verse) | dissolve | 1.0s |
| Low → High (verse → chorus) | hard cut | 0s |
| High → High (chorus → chorus) | hard cut | 0s |
| High → Low (chorus → verse) | fadeblack | 0.8s |
| Any → Bridge | dissolve | 1.2s |

Build the ffmpeg xfade filtergraph programmatically:

```python
def build_xfade_chain(clips, transitions, durations):
    filters = []
    cumulative = 0
    for i in range(len(clips) - 1):
        inp_a = f"[{i}:v]" if i == 0 else f"[v{i}]"
        inp_b = f"[{i+1}:v]"
        out = "" if i == len(clips) - 2 else f"[v{i+1}]"
        offset = cumulative + durations[i] - transitions[i]["duration"]
        cumulative = offset
        filters.append(
            f"{inp_a}{inp_b}xfade=transition={transitions[i]['type']}:duration={transitions[i]['duration']}:offset={offset:.2f}{out}"
        )
    return ";".join(filters)
```

### Step 6.4: Beat-Reactive Effects (Optional)

If user wants beat-reactive effects (on by default for EDM and hip-hop, off for ballads):

**Zoom pulse on beats** — 3% scale bump on each downbeat:
```
scale + crop with expression: 1 + 0.03 * pulse_envelope(beat_times)
```

**Brightness flash on onsets** — subtle flash on snare/kick hits:
```
eq=brightness='0.2 * pulse_envelope(onset_times)'
```

**Saturation boost during choruses:**
```
eq=saturation='1.0 + 0.4 * trapezoid_envelope(chorus_sections)'
```

For songs with 300+ beats, use chunked processing (split into 30s segments, process each, concatenate) to avoid ffmpeg expression length limits.

### Step 6.5: Mux Audio

```bash
ffmpeg -y -i captioned_video.mp4 -i "<original-audio>" \
  -c:v copy -c:a aac -b:a 320k -ar 48000 \
  -map 0:v:0 -map 1:a:0 -shortest \
  -movflags +faststart \
  "<output-dir>/final.mp4"
```

---

## Phase 7: Caption Rendering

### Path A: Karaoke Mode (ASS Subtitles)

Generate ASS file with `\kf` tags for smooth word-by-word color sweep:

```bash
python3 scripts/generate-ass.py \
  --aligned "<output-dir>/aligned-lyrics.json" \
  --output "<output-dir>/karaoke.ass" \
  --style karaoke
```

**Style presets:**
- `karaoke` — Gold highlight on white text, black outline, bottom-center (default)
- `neon` — Green highlight, orange outline, glow effect
- `minimal` — White fade-in, clean sans-serif, subtle

Burn into video:
```bash
ffmpeg -i video.mp4 -vf "ass=karaoke.ass" -c:v libx264 -crf 18 -c:a copy output.mp4
```

### Path B: Kinetic Typography Mode

9 templates available in `templates/`:

| Template | File | Best For |
|----------|------|----------|
| Bold Centered | `bold-centered.html` | Ballads, emotional, slow tempo |
| Dynamic Position | `dynamic-position.html` | Experimental, art pop |
| Text Slam | `01-text-slam.html` | Hip-hop, trailers, high energy |
| Typewriter | `02-typewriter-reveal.html` | Acoustic, indie, singer-songwriter |
| 3D Fly-In | `03-perspective-flyin.html` | Cinematic, epic, dramatic |
| Beat Pulse | `04-beat-pulse.html` | EDM, pop, anything with strong BPM |
| Split Scatter | `05-split-scatter.html` | Energetic pop, rock, fast lyrics |
| Gradient Wipe | `06-gradient-wipe.html` | R&B, pop ballads, sing-alongs |
| Stacked Emphasis | `07-stacked-emphasis.html` | Indie, art pop, emotional lyrics |

**Genre → template mapping (defaults, user can override):**

| Genre | Default Template |
|-------|-----------------|
| Pop | Beat Pulse or Gradient Wipe |
| Hip-hop | Text Slam |
| Rock | Split Scatter |
| EDM | Beat Pulse |
| R&B | Gradient Wipe |
| Indie | Stacked Emphasis or Typewriter |
| Country | Bold Centered |

Render frames via Puppeteer:

```bash
python3 scripts/render-kinetic-frames.py \
  --aligned "<output-dir>/aligned-lyrics.json" \
  --templates "templates/kinetic/" \
  --template "01-text-slam" \
  --output "<output-dir>/kinetic-frames/" \
  --fps 30
```

Composite over video:
```bash
ffmpeg -i video.mp4 -framerate 30 -i kinetic-frames/%04d.png \
  -filter_complex "[1:v]format=rgba[text];[0:v][text]overlay=0:0:format=auto:shortest=1" \
  -c:v libx264 -crf 18 -pix_fmt yuv420p -c:a copy output.mp4
```

---

## Phase 8: Export

### 8.1: YouTube Full Video (always)

```bash
ffmpeg -i final_with_captions.mp4 \
  -c:v libx264 -crf 18 -preset slow -profile:v high -pix_fmt yuv420p \
  -c:a aac -b:a 320k -ar 48000 \
  -movflags +faststart \
  "<song-name>-music-video.mp4"
```

### 8.2: YouTube Chapters (always)

Auto-generate chapter text from `song-structure.json`:

```
00:00 Intro
00:15 Verse 1
00:45 Chorus
01:15 Verse 2
...
```

Save to `<output-dir>/youtube-chapters.txt` for pasting into the video description.

### 8.3: TikTok Clips (if user requests)

Auto-select 3-5 best moments for vertical clips:

1. **Best hook** — highest-energy 15-30s segment (usually first chorus)
2. **Catchiest chorus** — the chorus section with highest average energy
3. **Bridge/climax** — the bridge or peak moment
4. **Quotable lines** — any lines the user tagged as quotable

For each clip:
```bash
# Center crop 16:9 → 9:16
ffmpeg -ss $START -t $DURATION -i final.mp4 \
  -vf "crop=ih*9/16:ih,scale=1080:1920" \
  -c:v libx264 -crf 18 -preset slow -profile:v high \
  -pix_fmt yuv420p -r 30 \
  -c:a aac -b:a 192k -ar 44100 \
  -movflags +faststart \
  "tiktok-clip-${N}.mp4"
```

For scenes with a character, use subject-aware cropping (MediaPipe face detection → smoothed crop position) instead of static center crop.

### 8.4: Thumbnail (always)

Select the most visually striking storyboard image (usually the chorus scene with highest prompt complexity). Save as `<output-dir>/thumbnail.png` at 1280x720.

---

## Working Directory

```
<audio-file-parent>/<song-name>-music-video/
├── whisper-raw.json              # Raw Whisper output
├── aligned-lyrics.json           # Word-level timestamps
├── beat-map.json                 # BPM, beats, onsets, energy
├── song-structure.json           # Section boundaries + labels
├── creative-brief.md             # Storyboard with prompts
├── stems/
│   ├── vocals.wav                # Isolated vocals
│   └── no_vocals.wav             # Instrumental
├── storyboard/
│   ├── scene-01.png ... scene-NN.png
│   └── storyboard-review.html    # Visual review page
├── clips/
│   ├── scene-01.mp4 ... scene-NN.mp4
│   └── qa-results.json           # Per-clip QA scores
├── normalized/
│   └── scene-01.mp4 ... scene-NN.mp4
├── karaoke.ass                   # (karaoke mode)
├── kinetic-frames/               # (music-video mode)
│   └── 0001.png ... NNNN.png
├── <song-name>-music-video.mp4   # Final YouTube output
├── youtube-chapters.txt          # Chapter markers
├── thumbnail.png                 # 1280x720 thumbnail
└── tiktok-clip-{1-5}.mp4        # Vertical clips (if requested)
```

---

## Error Handling

**fal.ai errors:**
- 422 (bad input) → check model-specific schema, fix and retry
- 429 (rate limit) → SDK auto-retries with backoff, up to 10 times
- 500 (server error) → auto-retried by SDK
- COMPLETED with error → check `result["error"]`, regenerate if content moderation
- Timeout → increase `client_timeout` or break into shorter clips

**Whisper fails:**
- OOM on large-v3 → fall back to medium → fall back to base
- Poor alignment → ask user to verify lyrics match the audio

**ffmpeg errors:**
- "Discarding non-monotonous DTS" → add `-fflags +genpts` before input
- xfade offset errors → verify offset < cumulative duration
- Memory issues with 20+ inputs → concatenate in batches of 5, then join batches

**Beat detection produces no beats:**
- Song has no clear rhythm → set beats to every 2 seconds, warn user
- Very slow tempo (<60 BPM) → cut scenes every 4-8 beats instead of 2-4

---

## Quality Standards

1. **Audio-visual sync** — every scene cut lands within 100ms of a detected beat
2. **Character consistency** — if reference provided and Kling 3.0 used, face similarity >0.50 (ArcFace cosine) across all clips
3. **No orphan lyrics** — every lyric line has a corresponding caption/kinetic text visible during its timestamp
4. **Transition coherence** — no black frames, no frozen frames, no audio gaps at scene boundaries
5. **Duration match** — final video duration within 1 second of original audio duration
6. **Resolution** — output is exactly 1920x1080 at 30fps (or 3840x2160 if upscaling was used)
7. **Encoding** — H.264, CRF 18, AAC 320kbps, movflags +faststart
8. **Cost accuracy** — actual fal.ai spend within 20% of the pre-generation estimate

---

## Critical Rules (Recap)

1. **Beat-sync is non-negotiable** — every scene cut MUST land on a detected beat.
2. **Motion-only prompts for i2v** — describe ONLY motion and camera. Never redescribe the character.
3. **Never fabricate lyrics** — user's text for words, Whisper for timing.
4. **QA every clip before assembly** — no unreviewed clips in the final video.
5. **Present cost estimate before spending money** — get confirmation first.
