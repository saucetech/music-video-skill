# Kinetic Typography Templates

## 9 Available Templates

| Template | File | Best For | Font Pairing |
|----------|------|----------|-------------|
| Bold Centered | `bold-centered.html` | Ballads, slow tempo | Montserrat + Inter |
| Dynamic Position | `dynamic-position.html` | Experimental, art pop | Montserrat + Inter |
| Text Slam | `01-text-slam.html` | Hip-hop, trailers, high energy | Bebas Neue + Playfair Display + Inter |
| Typewriter | `02-typewriter-reveal.html` | Acoustic, indie | JetBrains Mono + Courier Prime |
| 3D Fly-In | `03-perspective-flyin.html` | Cinematic, epic, dramatic | Oswald + Raleway |
| Beat Pulse | `04-beat-pulse.html` | EDM, pop, strong BPM | Montserrat + Lora |
| Split Scatter | `05-split-scatter.html` | Energetic pop, rock | Poppins + DM Serif Display |
| Gradient Wipe | `06-gradient-wipe.html` | R&B, pop ballads | Inter + Playfair Display |
| Stacked Emphasis | `07-stacked-emphasis.html` | Indie, art pop, emotional | Bebas Neue + Playfair Display + Inter |

## Genre → Template Defaults

| Genre | Default Template |
|-------|-----------------|
| Pop | Beat Pulse or Gradient Wipe |
| Hip-hop | Text Slam |
| Rock | Split Scatter |
| EDM | Beat Pulse |
| R&B | Gradient Wipe |
| Indie | Stacked Emphasis or Typewriter |
| Country | Bold Centered |

## Template Data Contract

Every template accepts:
- `window.LYRICS_DATA` — `{lines: [{text, start, end, words: [{text, start, end}]}], style: {primaryColor, accentColor, fontSize}}`
- `window.FRAME_TIME` — current time in seconds
- `window.renderFrame(time)` — triggers render for given timestamp

All templates render at 1920x1080 with transparent background.

## Rendering

```bash
python3 {baseDir}/scripts/render-kinetic-frames.py \
  --aligned aligned-lyrics.json \
  --templates "{baseDir}/templates/kinetic/" \
  --template "01-text-slam" \
  --output ./kinetic-frames/ \
  --fps 30
```

Composite over video:
```bash
ffmpeg -i video.mp4 -framerate 30 -i kinetic-frames/%04d.png \
  -filter_complex "[1:v]format=rgba[text];[0:v][text]overlay=0:0:format=auto:shortest=1" \
  -c:v libx264 -crf 18 -pix_fmt yuv420p -c:a copy output.mp4
```
