from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable, Sequence
import json
import math
import re

from open_storyline.mvp.ninerouter import NineRouterClient


MIN_SHORT_MS = 18_000
MAX_SHORT_MS = 25_000


class ShortsPlanError(RuntimeError):
    def __init__(self, code: str, message: str, *, rejected: list[dict[str, Any]] | None = None) -> None:
        self.code = code
        self.rejected = list(rejected or [])
        super().__init__(f"{code}: {message}")

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": str(self), "rejected": self.rejected}


@dataclass(frozen=True)
class ShortCandidate:
    start_ms: int
    end_ms: int
    title: str
    hook: str
    reason: str
    score: float

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["duration_ms"] = self.duration_ms
        return value


@dataclass(frozen=True)
class ShortsPlan:
    clips: list[ShortCandidate]
    rejected: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "clips": [clip.to_dict() for clip in self.clips],
            "rejected": self.rejected,
        }


def _clean_text(value: Any, *, fallback: str = "", limit: int = 240) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return (text or fallback)[:limit]


def _number(value: Any, *, integer: bool = False) -> float | int:
    if isinstance(value, bool):
        raise ValueError("boolean is not numeric")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("number is not finite")
    return int(round(parsed)) if integer else parsed


def _overlap_ratio(left: ShortCandidate, right: ShortCandidate) -> float:
    overlap = max(0, min(left.end_ms, right.end_ms) - max(left.start_ms, right.start_ms))
    return overlap / max(1, min(left.duration_ms, right.duration_ms))


def validate_candidates(
    raw_candidates: Iterable[Any],
    *,
    source_duration_ms: int,
    max_clips: int,
    min_duration_ms: int = MIN_SHORT_MS,
    max_duration_ms: int = MAX_SHORT_MS,
    max_overlap_ratio: float = 0.35,
) -> ShortsPlan:
    if source_duration_ms <= 0:
        raise ShortsPlanError("SOURCE_DURATION_INVALID", "source duration must be positive")
    if not 1 <= int(max_clips) <= 50:
        raise ShortsPlanError("MAX_CLIPS_INVALID", "max_clips must be between 1 and 50")

    accepted: list[ShortCandidate] = []
    rejected: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_candidates):
        try:
            if not isinstance(raw, dict):
                raise ValueError("candidate must be an object")
            start_ms = int(_number(raw.get("start_ms"), integer=True))
            end_ms = int(_number(raw.get("end_ms"), integer=True))
            duration_ms = end_ms - start_ms
            if start_ms < 0 or end_ms > source_duration_ms or end_ms <= start_ms:
                raise ValueError("timestamps are outside source bounds")
            if duration_ms < min_duration_ms or duration_ms > max_duration_ms:
                raise ValueError(f"duration must be {min_duration_ms}-{max_duration_ms} ms")
            score = float(_number(raw.get("score", 0.5)))
            if score < 0 or score > 1:
                raise ValueError("score must be between 0 and 1")
            candidate = ShortCandidate(
                start_ms=start_ms,
                end_ms=end_ms,
                title=_clean_text(raw.get("title"), fallback=f"Clip {index + 1}", limit=120),
                hook=_clean_text(raw.get("hook"), limit=240),
                reason=_clean_text(raw.get("reason"), limit=400),
                score=score,
            )
            accepted.append(candidate)
        except (TypeError, ValueError) as exc:
            rejected.append({"index": index, "reason": str(exc)[:300]})

    selected: list[ShortCandidate] = []
    for candidate in sorted(accepted, key=lambda item: (-item.score, item.start_ms, item.end_ms)):
        if any(_overlap_ratio(candidate, previous) > max_overlap_ratio for previous in selected):
            rejected.append({
                "start_ms": candidate.start_ms,
                "end_ms": candidate.end_ms,
                "reason": "overlaps a higher-ranked clip",
            })
            continue
        selected.append(candidate)

    selected = selected[:max_clips]

    if not selected:
        raise ShortsPlanError(
            "NO_VALID_SHORTS",
            "the remote model did not return any valid 18-25 second clips",
            rejected=rejected,
        )
    return ShortsPlan(clips=selected, rejected=rejected)


def _clock(milliseconds: int) -> str:
    total_seconds, millis = divmod(max(0, int(milliseconds)), 1000)
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def format_transcript(segments: Sequence[dict[str, Any]], text: str, limit: int = 120_000) -> str:
    lines = []
    for item in segments:
        content = _clean_text(item.get("text"), limit=1000)
        if not content:
            continue
        lines.append(f"[{_clock(int(item.get('start', 0)))}-{_clock(int(item.get('end', 0)))}] {content}")
    transcript = "\n".join(lines) or _clean_text(text, limit=limit)
    return transcript[:limit]


class ShortsPlanner:
    def __init__(self, client: NineRouterClient) -> None:
        self.client = client

    async def plan(
        self,
        *,
        editing_prompt: str,
        transcript_text: str,
        transcript_segments: Sequence[dict[str, Any]],
        source_duration_ms: int,
        max_clips: int,
        frame_data_urls: Sequence[str] = (),
    ) -> ShortsPlan:
        system_prompt = (
            "You are a precise social-video editor. Return only a JSON object with a clips array. "
            "Each clip must contain start_ms, end_ms, title, hook, reason, and score (0 to 1). "
            "Every duration must be between 18000 and 25000 milliseconds and stay within the source. "
            "Prefer self-contained hooks, useful or emotional moments, and avoid overlapping selections."
        )
        transcript = format_transcript(transcript_segments, transcript_text)
        user_payload = {
            "editing_prompt": _clean_text(editing_prompt, limit=12_000),
            "source_duration_ms": int(source_duration_ms),
            "maximum_output_clips": int(max_clips),
            "candidate_budget": min(150, max(3, int(max_clips) * 3)),
            "transcript": transcript,
        }
        response = await self.client.complete_json(
            system_prompt=system_prompt,
            user_prompt=json.dumps(user_payload, ensure_ascii=False),
            image_data_urls=frame_data_urls,
        )
        clips = response.get("clips")
        if not isinstance(clips, list):
            raise ShortsPlanError("SHORTS_RESPONSE_INVALID", "remote response must contain a clips array")
        return validate_candidates(
            clips,
            source_duration_ms=source_duration_ms,
            max_clips=max_clips,
        )
