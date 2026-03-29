# QA Pipeline Reference

## Tier 1: Face Consistency (DeepFace + ArcFace)

Run on storyboard images BEFORE video generation, and on video clips AFTER generation.

```python
from deepface import DeepFace

result = DeepFace.verify(
    img1_path=scene_image,
    img2_path=character_reference,
    model_name="ArcFace",
    distance_metric="cosine",
    enforce_detection=False,
)
similarity = 1.0 - result["distance"]
```

**Thresholds:**
- Storyboard images: similarity >= 0.45 (lower due to stylistic variation)
- Video clips: similarity >= 0.50

**Video clip face check** — extract frames at 2fps, compare each face to reference:
```python
cap = cv2.VideoCapture(clip_path)
fps = cap.get(cv2.CAP_PROP_FPS)
frame_interval = max(1, int(fps / 2))

scores = []
frame_idx = 0
while True:
    ret, frame = cap.read()
    if not ret:
        break
    if frame_idx % frame_interval == 0:
        try:
            result = DeepFace.verify(frame, reference, model_name="ArcFace",
                                      distance_metric="cosine", enforce_detection=False)
            scores.append(1.0 - result["distance"])
        except:
            pass
    frame_idx += 1
cap.release()
```

If DeepFace not installed → skip with warning, don't fail the pipeline.

## Tier 2: Temporal Consistency (SSIM)

Compare consecutive frames sampled at 2fps. SSIM below 0.65 between adjacent frames indicates a jarring temporal jump.

```python
from skimage.metrics import structural_similarity as ssim
import cv2

frame1_gray = cv2.cvtColor(frame1, cv2.COLOR_BGR2GRAY)
frame2_gray = cv2.cvtColor(frame2, cv2.COLOR_BGR2GRAY)
score = ssim(frame1_gray, frame2_gray)
# score > 0.65: OK, score < 0.65: flag as jarring
```

## Tier 3: Color Consistency (LAB Histograms)

Compare first frames of all clips to detect color temperature drift:

```python
lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
for channel in range(3):
    hist = cv2.calcHist([lab], [channel], None, [256], [0, 256])
    cv2.normalize(hist, hist)
# Correlate each clip's A/B channels against the group average
# Flag if correlation < 0.7
```

## Tier 4: Vision LLM (Gemini 2.5 Flash, optional)

Send 3-5 frames per clip:

```
Analyze these AI-generated video frames for quality issues.
Check for:
1. Extra or missing fingers, twisted joints, duplicated limbs
2. Face deformation, melting features, asymmetric eyes
3. Objects morphing between frames
4. Unnatural motion or physics violations
5. Text or watermarks that shouldn't be there

Score overall quality 1-5 (5 = no issues, 1 = severe artifacts).
Return JSON: {"score": N, "issues": ["description", ...]}
```

## Auto-Retry Strategy

```
If QA fails:
  → Attempt 1: Same prompt, new random seed
  → Attempt 2: Same prompt, another new seed
  → Attempt 3: Simplified prompt (reduce motion complexity)
  → If still failing: flag for user review with best attempt
```

## Running QA

```bash
# Single clip
python3 {baseDir}/scripts/qa-check.py \
  --clip scene-01.mp4 \
  --reference-face character-front.png \
  --output qa-result.json

# All clips
python3 {baseDir}/scripts/qa-check.py \
  --clips-dir ./clips/ \
  --reference-face character-front.png \
  --output qa-results.json
```
