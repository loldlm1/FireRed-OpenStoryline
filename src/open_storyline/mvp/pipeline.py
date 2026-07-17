from __future__ import annotations

from pathlib import Path
from typing import Any
import asyncio
import json

from open_storyline.config import Settings
from open_storyline.mvp.ffmpega import EffectsPlanner, FFMPEGAClient, ffmpega_enabled
from open_storyline.mvp.jobs import JobStore
from open_storyline.mvp.ninerouter import NineRouterClient
from open_storyline.mvp.render import (
    CPUShortRenderer,
    RenderSettings,
    extract_frame_data_urls,
    probe_media,
)
from open_storyline.mvp.shorts import ShortsPlanner
from open_storyline.utils.remote_stt import MistralSTTClient, RemoteSTTError, extract_audio_for_stt


class MVPJobProcessor:
    """Remote-inference pipeline; local work is restricted to deterministic FFmpeg."""

    def __init__(self, config: Settings) -> None:
        self.config = config

    async def __call__(self, job_id: str, store: JobStore) -> dict[str, Any]:
        state = store.load(job_id)
        source = store.source_path(job_id)
        work_dir = store.work_dir(job_id)
        output_dir = store.output_dir(job_id)

        media = await asyncio.to_thread(probe_media, source)
        if not media.has_audio:
            raise RemoteSTTError("MEDIA_HAS_NO_AUDIO", "source video must contain an audio stream")
        store.update(job_id, progress=0.18, stage="extracting_audio")
        audio = await asyncio.to_thread(extract_audio_for_stt, source, work_dir / "audio.mp3")

        store.update(job_id, progress=0.28, stage="remote_transcription")
        stt = MistralSTTClient.from_config(self.config.remote_asr)
        transcript = await stt.transcribe(audio, language=self.config.remote_asr.language)
        transcript_path = output_dir / "transcript.json"
        transcript_path.write_text(json.dumps({
            "model": transcript.model,
            "text": transcript.text,
            "segments": transcript.segments,
            "attempts": [attempt.to_dict() for attempt in transcript.attempts],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        store.register_artifact(job_id, transcript_path, kind="transcript")

        store.update(job_id, progress=0.48, stage="sampling_frames")
        frames = await asyncio.to_thread(
            extract_frame_data_urls,
            source,
            duration_ms=media.duration_ms,
            count=self.config.mvp.frame_count,
        )
        store.update(job_id, progress=0.58, stage="remote_planning")
        planner = ShortsPlanner(NineRouterClient.from_config(self.config.ninerouter))
        plan = await planner.plan(
            editing_prompt=state["prompt"],
            transcript_text=transcript.text,
            transcript_segments=transcript.segments,
            source_duration_ms=media.duration_ms,
            max_clips=int((state.get("request") or {}).get("max_clips") or 8),
            frame_data_urls=frames,
        )

        store.update(job_id, progress=0.68, stage="rendering")
        renderer = CPUShortRenderer(RenderSettings(
            width=self.config.mvp.render_width,
            height=self.config.mvp.render_height,
            fps=self.config.mvp.render_fps,
            preset=self.config.mvp.render_preset,
            crf=self.config.mvp.render_crf,
        ))
        rendered = await asyncio.to_thread(
            renderer.render_plan,
            source=source,
            clips=plan.clips,
            transcript_segments=transcript.segments,
            destination_dir=output_dir,
        )
        effects_plan = None
        final_outputs = []
        ffmpega = None
        if ffmpega_enabled(self.config.ffmpega):
            store.update(job_id, progress=0.88, stage="planning_effects")
            effects_plan = await EffectsPlanner(
                NineRouterClient.from_config(self.config.ninerouter)
            ).plan(state["prompt"])
            if effects_plan.effects:
                ffmpega = FFMPEGAClient.from_config(self.config.ffmpega)
        for item in rendered:
            final_video = item.video_path
            if ffmpega is not None and effects_plan is not None:
                enhanced = item.video_path.with_name(f"{item.video_path.stem}-effects.mp4")
                final_video = await ffmpega.apply(
                    source=item.video_path,
                    destination=enhanced,
                    plan=effects_plan,
                )
                item.video_path.unlink(missing_ok=True)
            store.register_artifact(job_id, final_video, kind="video")
            if item.subtitle_path is not None:
                store.register_artifact(job_id, item.subtitle_path, kind="subtitles")
            final_outputs.append({
                "video": final_video.name,
                "subtitles": item.subtitle_path.name if item.subtitle_path else None,
                "clip": item.clip.to_dict(),
            })

        manifest_path = output_dir / "manifest.json"
        manifest_path.write_text(json.dumps({
            "job_id": job_id,
            "source": {
                "filename": state["input"]["original_filename"],
                "duration_ms": media.duration_ms,
                "width": media.width,
                "height": media.height,
            },
            "stt": {
                "model": transcript.model,
                "attempts": [attempt.to_dict() for attempt in transcript.attempts],
            },
            "plan": plan.to_dict(),
            "effects": effects_plan.to_dict() if effects_plan is not None else {"effects": []},
            "outputs": final_outputs,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        store.register_artifact(job_id, manifest_path, kind="manifest")
        return {
            "stage": "completed",
            "stt_model": transcript.model,
            "clip_count": len(rendered),
        }
