# Beat-Sync Reference

## Transition Types by Section Energy

| Energy Transition | Cut Type | Duration |
|-------------------|----------|----------|
| Low → Low (verse → verse) | dissolve | 1.0s |
| Low → High (verse → chorus) | hard cut | 0s |
| High → High (chorus → chorus) | hard cut | 0s |
| High → Low (chorus → verse) | fadeblack | 0.8s |
| Any → Bridge | dissolve | 1.2s |
| Bridge → Any | dissolve | 1.0s |
| Default | dissolve | 0.8s |

## Beat-Reactive Effects

### BPM Intensity Scaling

| BPM Range | Zoom | Flash | Rationale |
|-----------|------|-------|-----------|
| < 100 | 3.0% | 0.20 | Full intensity, beats are sparse |
| 100-140 | 3.0% | 0.20 | Standard |
| 140-170 | 1.8% | 0.12 | Reduced — beats arrive fast |
| > 170 | 1.2% | 0.08 | Minimal — or disable zoom |

### Effect Descriptions

**Zoom pulse** — scale bump on each beat with exponential decay:
```
scale expression: 1 + amplitude * max(0, 1-(t-beat)/0.15) * between(t, beat, beat+0.15)
```

**Brightness flash** — brightness boost on each onset:
```
eq=brightness='amplitude * max(0, 1-(t-onset)/0.1) * between(t, onset, onset+0.1)'
```

**Saturation boost** — during chorus sections with smooth ramp:
```
eq=saturation='1.0 + 0.4 * clip((t-start)/1.0, 0, 1) * clip((end-t)/1.0, 0, 1)'
```

### Running Beat-Reactive Effects

```bash
python3 {baseDir}/scripts/beat-reactive-ffmpeg.py \
  --input video.mp4 \
  --beat-map beat-map.json \
  --song-structure song-structure.json \
  --output video-with-effects.mp4 \
  --effects zoom,flash,saturation
```

### Chunking for Long Songs

Songs with 300+ beats exceed ffmpeg's expression length limit (~8-16KB). The script auto-detects this and splits into 30-second segments, each with its own filtergraph, then concatenates.

## Assembly Script

```bash
bash {baseDir}/scripts/assemble.sh \
  --clips ./clips/ \
  --audio song.mp3 \
  --mode karaoke|music-video \
  --captions ./karaoke.ass OR ./kinetic-frames/ \
  --beat-map ./beat-map.json \
  --song-structure ./song-structure.json \
  --output ./final.mp4 \
  --beat-effects
```

Steps: normalize clips → color normalization → beat-snapped xfade chain → beat-reactive effects (optional) → caption overlay → audio mux → verify.
