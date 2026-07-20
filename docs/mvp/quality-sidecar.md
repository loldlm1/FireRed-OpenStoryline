# Optional Reference Quality Sidecar

The remote MVP keeps perceptual research tooling outside `Dockerfile.remote`.
`Dockerfile.quality` builds a separate, non-root evaluator for operator and
canary analysis. It compares a delivery encode with a plan-aligned reference;
it must not compare an original horizontal source directly with a final
portrait edit containing intentional crops, captions, or overlays.
The current pinned build targets the production host's `amd64` architecture.

## Pinned provenance

| Component | Version | Source integrity | License |
| --- | --- | --- | --- |
| Python base | 3.11 slim | image digest in `Dockerfile.quality` | PSF and bundled distribution licenses |
| FFmpeg | 7.1.1 | SHA-256 `733984395e0dbbe5c046abda2dc49a5544e7e0e1e2366bba849222ae9e3a03b1` | LGPL-2.1-or-later for this non-GPL build |
| Netflix VMAF/libvmaf | 3.0.0 | SHA-256 `7178c4833639e6b989ecae73131d02f70735fdb3fc2c7d84bc36c9c3461d93b1` | BSD-2-Clause-Patent |
| `vmaf_v0.6.1.json` | VMAF v0.6.1 | SHA-256 `5950d61fa1f861bd45d8149d80539ed9f3376cfc2495b8f0fa8e9f57cb131ee3` | BSD-2-Clause-Patent |
| ffmpeg-quality-metrics | 3.12.0 | wheel hash in `requirements-quality.txt` | MIT |
| ffmpeg-progress-yield | 1.1.3 | wheel hash in `requirements-quality.txt` | MIT |
| tqdm | 4.69.0 | wheel hash in `requirements-quality.txt` | MPL-2.0 and MIT |

The shipped image deliberately excludes QCTools/qcli, DOVER, FAST-VQA, model
weights, OpenCV, provider clients, application configuration, and provider
secrets. QCTools remains an optional GPLv3 operator workstation tool. DOVER and
FAST-VQA remain excluded because their non-commercial research terms do not fit
the shipped production components.

## Build and analyze

```bash
bin/quality-mvp build
bin/quality-mvp analyze /path/reference.mp4 /path/candidate.mp4 \
  --execution /path/render_execution.json \
  --format json > /tmp/reference-quality.json
```

The operator wrapper runs the container with:

- network disabled and no environment/provider secrets;
- a read-only root filesystem and read-only bind-mounted inputs;
- all Linux capabilities dropped and `no-new-privileges` enabled;
- a fixed non-root UID/GID, bounded processes, two CPUs, 2 GiB memory, and a
  maximum one-hour operator timeout;
- temporary metric files on an isolated in-memory `/tmp` mount only.

JSON reports contain pooled metrics, bounded worst-frame records, and per-frame
VMAF, SSIM, PSNR, VIF, and XPSNR. When `render_execution.json` is supplied,
worst frames include only allowlisted clip, segment, operation, strategy, and
timeline fields. CSV output contains the same per-frame metric rows. Neither
format includes provider responses, prompts, transcripts, source hashes, or
input filenames or absolute paths.

## Threshold policy

VMAF and XPSNR are optional evidence, not current promotion blockers. Calibrate
thresholds across multiple synthetic degradation classes and private canary
reviews before adding an enforcement rule. The built-in FFmpeg crop, caption,
structure, SSIM, and PSNR gates remain authoritative if the quality image is
unavailable.

## Explicit re-edit feedback

A new run may explicitly request sanitized quality evidence from one completed
or failed prior attempt of the same immutable prompt version:

```json
{
  "prior_attempt_id": "REPLACE_WITH_PRIOR_RUN_ID",
  "use_quality_feedback": true
}
```

The server reads already-ingested audit documents and gives the planner only
versioned blocker codes, invalid crop windows, active-picture ratios, caption
footprint summaries, and worst aligned metric timestamps. Raw audit text,
captions, prompts, transcripts, provider bodies, paths, and semantic target
descriptions are excluded. The resulting job records the prior attempt and
feedback version in its immutable request snapshot and manifest.

This mechanism never retries a post-render creative revision inside the same
job. Each revision is a separately attributable attempt with its own provider
attempt count, latency, token usage, and provider-reported cost when available.
