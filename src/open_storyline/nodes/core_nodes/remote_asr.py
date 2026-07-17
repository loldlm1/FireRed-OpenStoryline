from __future__ import annotations

from pathlib import Path
from typing import Any, Dict
import tempfile

from open_storyline.nodes.core_nodes.base_node import BaseNode, NodeMeta
from open_storyline.nodes.node_schema import RemoteASRInput
from open_storyline.nodes.node_state import NodeState
from open_storyline.utils.register import NODE_REGISTRY
from open_storyline.utils.remote_stt import MistralSTTClient, extract_audio_for_stt


@NODE_REGISTRY.register()
class RemoteASRNode(BaseNode):
    meta = NodeMeta(
        name="remote_asr",
        description="Transcribe video clips directly with Mistral Voxtral",
        node_id="remote_asr",
        node_kind="asr",
        require_prior_kind=["split_shots"],
        default_require_prior_kind=["split_shots"],
        next_available_node=["speech_rough_cut"],
    )
    input_schema = RemoteASRInput

    def __init__(self, server_cfg) -> None:
        super().__init__(server_cfg)
        self.stt: MistralSTTClient | None = None

    async def default_process(self, node_state: NodeState, inputs: Dict[str, Any]) -> Any:
        return {"asr_infos": []}

    async def process(self, node_state: NodeState, inputs: Dict[str, Any]) -> Any:
        if self.stt is None:
            self.stt = MistralSTTClient.from_config(self.server_cfg.remote_asr)
        language = str(inputs.get("language") or self.server_cfg.remote_asr.language or "")
        asr_infos = []
        for clip in inputs["split_shots"].get("clips", []):
            if clip.get("kind") != "video":
                continue
            with tempfile.TemporaryDirectory(prefix="openstoryline-stt-") as tmpdir:
                audio_path = extract_audio_for_stt(clip["path"], Path(tmpdir) / "audio.mp3")
                result = await self.stt.transcribe(audio_path, language=language)
            asr_infos.append({
                "clip_id": clip["clip_id"],
                "path": clip["path"],
                "kind": clip["kind"],
                "source_ref": clip.get("source_ref", {}),
                "fps": clip.get("fps", 30),
                "asr_text": result.text,
                "asr_timestamps": result.timestamps,
                "asr_sentence_info": result.segments,
                "stt_model": result.model,
                "stt_attempts": [attempt.to_dict() for attempt in result.attempts],
            })
            node_state.node_summary.info_for_user(
                f"Remote transcription completed with {result.model}"
            )
        return {"asr_infos": asr_infos}
