# Export Format Specifications

## YouTube Full Video

```bash
ffmpeg -i input.mp4 \
  -c:v libx264 -crf 18 -preset slow -profile:v high -pix_fmt yuv420p \
  -c:a aac -b:a 320k -ar 48000 \
  -movflags +faststart \
  output.mp4
```

Resolution: 1920x1080 (or 3840x2160 if upscaled)
FPS: 24 (cinematic genres) or 30 (pop/EDM)

## YouTube Chapters

Auto-generate from `song-structure.json`:
```
00:00 Intro
00:15 Verse 1
00:45 Chorus
01:15 Verse 2
01:45 Chorus
02:15 Bridge
02:45 Final Chorus
03:15 Outro
```

Save to `youtube-chapters.txt`.

## TikTok Clips (9:16)

```bash
ffmpeg -ss $START -t $DURATION -i input.mp4 \
  -vf "crop=ih*9/16:ih,scale=1080:1920" \
  -c:v libx264 -crf 18 -preset slow -profile:v high -level:v 4.1 \
  -pix_fmt yuv420p -r 30 \
  -c:a aac -b:a 192k -ar 44100 \
  -movflags +faststart \
  tiktok-clip.mp4
```

For subject-aware cropping (face tracking):
```bash
python3 {baseDir}/scripts/vertical-crop.py \
  --video final.mp4 \
  --song-structure song-structure.json \
  --beat-map beat-map.json \
  --output-dir ./tiktok-clips/ \
  --num-clips 5 \
  --smart-crop
```

**Clip selection priority:**
1. First chorus (best hook)
2. Highest-energy chorus
3. Bridge/climax
4. Strong opener (if high energy)
5. User-tagged quotable lines

Each clip: 15-30 seconds, beat-aligned start/end.

## YouTube Shorts

Same as TikTok but higher quality:
- CRF 16 (vs 18)
- AAC 256k (vs 192k)
- 48kHz (vs 44.1kHz)
- Max duration: 3 minutes

## Thumbnail

Select the most visually striking storyboard image (usually the chorus scene).
Save as `thumbnail.png` at 1280x720.
