# Perception Stack Setup (Omen GPU + Moondream)

*How the GPU inference + VLM-grounding stack was brought up on `omen` (RTX 5060 Ti,
Blackwell `sm_120`, Ubuntu 26.04). Written 2026-06-06. The hard parts were the
Blackwell torch build, a flaky NIC, and torch/transformers/torchvision version
pins — all documented below so we don't re-derive them.*

This stack lives in a **dedicated venv**, separate from the substrate's `.venv`,
matching foundations' "perception daemon as a separate process" design (its own
GPU-memory ownership, independent restart, and it keeps the substrate venv lean).

---

## TL;DR — the working recipe

```bash
# 1. Dedicated perception venv (Python 3.12, uv-managed)
uv venv --python 3.12 ~/perception-venv
V=~/perception-venv/bin/python

# 2. torch for Blackwell (sm_120) — needs cu128 wheels
uv pip install --python $V torch --torch-backend=cu128        # -> torch 2.11.0+cu128

# 3. Moondream deps. transformers MUST be <5 (see gotcha #2).
uv pip install --python $V numpy "transformers<5" accelerate einops pillow safetensors timm

# 4. torchvision MUST be the +cu128 build (see gotcha #3) — install explicitly,
#    --no-deps so the resolver can't swap it back to a mismatched CUDA build.
uv pip install --python $V --no-deps \
    https://download.pytorch.org/whl/cu128/torchvision-0.26.0+cu128-cp312-cp312-manylinux_2_28_x86_64.whl
```

Verify:
```python
import torch, torchvision
assert torch.cuda.is_available() and torch.cuda.get_device_capability(0) == (12, 0)
from transformers import AutoModelForCausalLM
m = AutoModelForCausalLM.from_pretrained(
    "vikhyatk/moondream2", revision="2025-06-21",
    trust_remote_code=True, device_map={"": "cuda"})
img = ...  # PIL.Image
m.point(img, "green block")["points"]    # [{'x': 0.43, 'y': 0.67}]  (normalized)
m.detect(img, "green block")["objects"]  # [{'x_min':..,'y_min':..,'x_max':..,'y_max':..}]
m.caption(img, length="short")["caption"]
```

Versions that work together (2026-06-06): **torch 2.11.0+cu128, torchvision
0.26.0+cu128, transformers 4.57.6, numpy 2.4.6**, moondream2 rev `2025-06-21`.

---

## Gotcha #1 — flaky NIC + non-resuming installers

Omen's onboard NIC is committed to the Franka direct link (`enp8s0`, the
dnsmasq-shared profile in `panda-setup.md`), so all internet rides the wifi card
— which is flaky and drops connections intermittently. **uv and pip do not
resume partial downloads**, so a 600 MB CUDA wheel that drops at 90% restarts
from zero and *never converges* on a dropping link. The cu128 torch stack is
~2 GB of wheels, two of them huge (cudnn 627 MB, cublas 567 MB).

**Workaround:** prefetch the big wheels with `curl` (resume + retry + forced
IPv4), then install from the local files:

```bash
curl -4 -C - --retry 50 --retry-all-errors --retry-delay 3 -O "<wheel-url>"
# (-C - resumes; every dropped byte is kept instead of restarting)
uv pip install --python $V --find-links /tmp/wheels /tmp/wheels/*.whl
```

The torch wheel URL on the pytorch index contains `%2B` (URL-encoded `+`); rename
the saved file `%2B` → `+` or uv rejects it as an invalid version. A USB wifi
adapter (better radio) is the real fix; curl-resume is the durable fallback.

## Gotcha #2 — transformers must be < 5 for moondream2

moondream2's `trust_remote_code` modeling file does
`from transformers import PreTrainedModel, PretrainedConfig`, which transformers
**5.x** broke (reorganized lazy imports). The error is masked as
`ModuleNotFoundError: Could not import module 'PreTrainedModel'`. Pin
`transformers<5` (resolves to 4.57.x).

## Gotcha #3 — torchvision must match torch's CUDA (cu128)

`uv pip install ... --torch-backend=cu128` only special-cases **torch**, not
**torchvision** — so torchvision gets pulled from PyPI built for the default
CUDA (13.0), and its startup check raises
`RuntimeError: PyTorch and torchvision were compiled with different CUDA major
versions` (12.8 vs 13.0). That `RuntimeError` (not an `ImportError`) propagates
through `transformers.image_utils` and re-surfaces as the same masked
`PreTrainedModel` error from gotcha #2. Fix: install the `+cu128` torchvision
explicitly from the pytorch index with `--no-deps` (recipe step 4). `timm`
pulls torchvision in, which is what dragged the mismatched build in.

---

## Verified (2026-06-06)

- CUDA compute on Blackwell: `torch.randn(2000,2000,device="cuda") @ ...` runs;
  `get_device_capability == (12, 0)`.
- Moondream2 on GPU localizes the four desk blocks correctly: `point()` returns
  green/blue/tan/red in the right left-to-right order, each landing on its block
  (annotated overlay confirmed). This is the localization primitive for the
  grasp task and the grounding for perceptual verification (foundations #1).

## Not done yet

- Moondream runs **in-process** in this test venv. The foundations design is a
  separate **perception daemon** (FastAPI/ZMQ) the substrate calls; not built.
- No substrate primitive wraps Moondream yet (a `locate(predicate)` /
  `vlm_grounded` body). Next after the camera→robot geometry exists.
