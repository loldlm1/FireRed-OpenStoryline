# Remote MVP Render Quality Profiles

The remote MVP uses named H.264/AAC/yuv420p profiles so every render records a
reproducible quality choice. `OPENSTORYLINE_RENDER_QUALITY_PROFILE` selects the
profile and `OPENSTORYLINE_RENDER_FPS_CAP` places an upper bound on source frame
rate preservation.

| Profile | H.264 settings | Frame-rate policy | Intended use |
| --- | --- | --- | --- |
| `legacy` | CRF 23, `veryfast` | preserve up to 30 fps | temporary rollback |
| `balanced` | CRF 20, `fast` | preserve up to 30 fps | constrained CPU capacity |
| `high` | CRF 18, `medium` | preserve up to 60 fps | production quality canary |

The selected production canary is `high`. It keeps the original 50/60 fps
motion when present instead of forcing every source to 30 fps. The job timeout
and active-job capacity remain the outer CPU and latency bounds.

## Synthetic Benchmark

The Sprint 3 benchmark used local FFmpeg 4.4.2 on a deterministic 360x640,
30 fps, four-second detailed test pattern. Each delivery encode was compared
with the aligned FFV1 reference. Times are local measurements, not VPS capacity
guarantees.

| Profile | Wall time | Bytes | SSIM | PSNR |
| --- | ---: | ---: | ---: | ---: |
| `legacy` | 0.61 s | 536,555 | 0.973600 | 29.3115 |
| `balanced` | 0.90 s | 733,934 | 0.982122 | 29.5957 |
| `high` | 1.07 s | 834,836 | 0.983176 | 29.6474 |

Compared with `legacy`, `high` reduced the remaining SSIM structural error by
about 36%, while increasing local wall time by about 75% and file size by about
56%. A separate two-second 60 fps fixture produced 120 frames with `high`; the
legacy path produced 62 frames at 30 fps. Production evidence is written to
`render_quality_profile.json` with source/output FPS, bitrate, bytes, profile,
CRF, preset, and encode time.

This profile change does not excuse framing defects. Crop coverage, caption
footprint, required assets, and later promotion gates remain independent
fail-closed checks.

## Rollback

Set `OPENSTORYLINE_RENDER_QUALITY_PROFILE=legacy` and
`OPENSTORYLINE_RENDER_FPS_CAP=30`, redeploy, and verify `/up`. The public SRT
artifact remains unchanged; render-only ASS files provide explicit output
coordinates for corrected caption placement.
