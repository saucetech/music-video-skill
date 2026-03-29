# fal.ai API Patterns

## Authentication

Set `FAL_KEY` environment variable. The `fal-client` Python SDK reads it automatically.

```bash
export FAL_KEY="your-key"
pip install fal-client
```

## File Upload

```python
import fal_client

url = fal_client.upload_file("/path/to/image.png")
# Returns: https://v3.fal.media/files/...
```

## SEEDREAM v4.5 (Text-to-Image)

Endpoint: `fal-ai/bytedance/seedream/v4.5/text-to-image` — $0.04/image

```python
result = fal_client.subscribe(
    "fal-ai/bytedance/seedream/v4.5/text-to-image",
    arguments={
        "prompt": "cinematic film still, anamorphic lens, ...",
        "negative_prompt": "blurry, low quality, distorted, watermark, text, logo, extra fingers, deformed hands, multiple people, cloned faces, plastic skin, overexposed, underexposed, jpeg artifacts, oversharpened, oversaturated, symmetry errors, 3D render, cartoon, anime",
        "image_size": "landscape_16_9",
        "num_images": 1,
        "seed": 42,
    },
)
image_url = result["images"][0]["url"]
```

## Kling 3.0 Pro (Image-to-Video with Element Binding)

Endpoint: `fal-ai/kling-video/v3/pro/image-to-video` — $0.112/sec

```python
result = fal_client.subscribe(
    "fal-ai/kling-video/v3/pro/image-to-video",
    arguments={
        "prompt": "@Element1 walks forward confidently, slow dolly push-in, warm cinematic lighting",
        "start_image_url": storyboard_image_url,
        "elements": [{
            "frontal_image_url": character_frontal_url,
            "reference_image_urls": [side_url, profile_url],
        }],
        "duration": "5",
        "negative_prompt": "face morphing, identity change, deformed face, flickering, blurry",
        "cfg_scale": 0.5,
    },
)
video_url = result["video"]["url"]
```

## Kling 3.0 Standard (Image-to-Video)

Endpoint: `fal-ai/kling-video/v3/standard/image-to-video` — $0.084/sec

Same parameters as Pro. Lower visual fidelity, same element binding support.

## Kling 2.6 Pro (Image-to-Video)

Endpoint: `fal-ai/kling-video/v2.6/pro/image-to-video` — $0.07/sec

```python
result = fal_client.subscribe(
    "fal-ai/kling-video/v2.6/pro/image-to-video",
    arguments={
        "prompt": "Slow push-in, gentle wind in hair, warm light",
        "image_url": storyboard_image_url,
        "duration": "5",
        "aspect_ratio": "16:9",
    },
)
```

**No element binding.** Character consistency relies on the storyboard image only.

## Image Upscaling

**SeedVR2** — $0.001/megapixel (best for AI art):
```python
result = fal_client.subscribe(
    "fal-ai/seedvr/upscale/image",
    arguments={"image_url": url, "scale_factor": 2},
)
```

**CCSR** — FREE:
```python
result = fal_client.subscribe(
    "fal-ai/ccsr",
    arguments={"image_url": url, "scale": 2},
)
```

## Parallel Submission

Submit all scenes at once — fal.ai handles queuing server-side:

```python
handles = []
for scene in scenes:
    handle = fal_client.submit(endpoint, arguments=scene_args)
    handles.append((scene, handle))

for scene, handle in handles:
    result = handle.get()  # blocks per handle
    download(result["video"]["url"], f"clips/scene-{scene['num']:02d}.mp4")
```

## Cost Estimation

```python
# Before generating, estimate total cost
image_cost = num_scenes * 0.04
video_cost = num_scenes * avg_clip_duration * cost_per_sec
total = image_cost + video_cost
```

| Model | Cost/sec |
|-------|----------|
| Kling 3.0 Pro | $0.112 |
| Kling 3.0 Standard | $0.084 |
| Kling 2.6 Pro | $0.07 |
