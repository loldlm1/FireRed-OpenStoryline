from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse
import json
import os
import re

import httpx

from open_storyline.mvp.edit_plan import AssetRequest


PEXELS_API_BASE_URL = "https://api.pexels.com"
PEXELS_LICENSE_URL = "https://www.pexels.com/license/"
PEXELS_RIGHTS_NOTICE = (
    "Pexels media remains subject to the current Pexels license. Preserve creator "
    "and source provenance, and require an operator rights review before publication."
)
PEXELS_SEARCH_HOST = "api.pexels.com"
PEXELS_DOWNLOAD_HOSTS = frozenset({"images.pexels.com", "videos.pexels.com"})
PEXELS_SOURCE_HOSTS = frozenset({"www.pexels.com", "pexels.com"})


class PexelsError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        attempts: Iterable["PexelsAttempt"] = (),
    ) -> None:
        self.code = code
        self.attempts = tuple(attempts)
        super().__init__(f"{code}: {message}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": str(self),
            "attempts": [attempt.to_dict() for attempt in self.attempts],
        }


@dataclass(frozen=True)
class PexelsAttempt:
    number: int
    operation: str
    status_code: int | None
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PexelsAsset:
    provider_id: int
    kind: str
    content: bytes
    extension: str
    content_type: str
    creator: str
    creator_url: str
    source_url: str
    media_url: str
    width: int
    height: int
    duration_seconds: float | None
    file_size: int
    retrieved_at: str
    attempts: tuple[PexelsAttempt, ...]

    def provenance(self) -> dict[str, Any]:
        return {
            "pexels_asset_id": self.provider_id,
            "creator": self.creator,
            "creator_url": self.creator_url,
            "source_url": self.source_url,
            "selected_file": {
                "media_url": self.media_url,
                "content_type": self.content_type,
                "width": self.width,
                "height": self.height,
                "duration_seconds": self.duration_seconds,
                "bytes": self.file_size,
            },
            "retrieved_at": self.retrieved_at,
            "license_url": PEXELS_LICENSE_URL,
        }


def _clean(value: Any, *, limit: int = 300) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = re.sub(r"(?i)bearer\s+[a-z0-9._~+\-/=]+", "Bearer ***", text)
    return text[:limit]


def _transport_reason(exc: BaseException) -> str:
    return type(exc).__name__[:80]


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    value = raw.strip().lower()
    if value not in {"1", "true", "yes", "on", "0", "false", "no", "off"}:
        raise PexelsError("PEXELS_CONFIG_INVALID", f"{name} must be true or false")
    return value in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    try:
        return int(raw if raw is not None else default)
    except (TypeError, ValueError) as exc:
        raise PexelsError("PEXELS_CONFIG_INVALID", f"{name} must be an integer") from exc


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    try:
        return float(raw if raw is not None else default)
    except (TypeError, ValueError) as exc:
        raise PexelsError("PEXELS_CONFIG_INVALID", f"{name} must be numeric") from exc


def pexels_enabled(config: Any) -> bool:
    return _env_bool(
        "OPENSTORYLINE_PEXELS_ENABLED",
        bool(getattr(config, "pexels_enabled", False)),
    )


def pexels_server_cap(config: Any) -> int:
    raw = os.getenv("OPENSTORYLINE_MAX_STOCK_ASSETS_PER_CLIP")
    try:
        value = int(
            raw
            if raw is not None
            else getattr(config, "max_stock_assets_per_clip", 2)
        )
    except (TypeError, ValueError) as exc:
        raise PexelsError(
            "PEXELS_CONFIG_INVALID",
            "OPENSTORYLINE_MAX_STOCK_ASSETS_PER_CLIP must be an integer",
        ) from exc
    if not 0 <= value <= 8:
        raise PexelsError("PEXELS_CONFIG_INVALID", "stock asset cap must be between 0 and 8")
    return value


def pexels_license_review_date(config: Any) -> date:
    value = str(
        os.getenv("OPENSTORYLINE_PEXELS_LICENSE_REVIEWED_AT")
        or getattr(config, "pexels_license_reviewed_at", "")
    ).strip()
    try:
        reviewed = date.fromisoformat(value)
    except ValueError as exc:
        raise PexelsError(
            "PEXELS_LICENSE_REVIEW_REQUIRED",
            "a Pexels license review date in YYYY-MM-DD format is required",
        ) from exc
    today = datetime.now(timezone.utc).date()
    age = (today - reviewed).days
    if age < 0 or age > 180:
        raise PexelsError(
            "PEXELS_LICENSE_REVIEW_REQUIRED",
            "the Pexels license review must be current within 180 days",
        )
    return reviewed


def _safe_url(
    value: Any,
    *,
    allowed_hosts: frozenset[str],
    code: str,
) -> str:
    url = _clean(value, limit=2048)
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.hostname.lower() not in allowed_hosts
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise PexelsError(code, "Pexels returned an untrusted URL")
    return url


def _positive_int(value: Any, *, maximum: int, code: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise PexelsError(code, "Pexels returned invalid numeric metadata") from exc
    if not 1 <= parsed <= maximum:
        raise PexelsError(code, "Pexels returned out-of-range numeric metadata")
    return parsed


def _required_text(value: Any, *, limit: int, code: str) -> str:
    text = _clean(value, limit=limit)
    if not text:
        raise PexelsError(code, "Pexels returned missing text metadata")
    return text


class PexelsClient:
    def __init__(
        self,
        *,
        api_key: str,
        search_limit: int = 8,
        timeout: float = 30.0,
        max_retries: int = 2,
        max_bytes: int = 80 * 1024 * 1024,
        max_video_duration_seconds: int = 60,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_key = str(api_key or "").strip()
        self.search_limit = int(search_limit)
        self.timeout = float(timeout)
        self.max_retries = int(max_retries)
        self.max_bytes = int(max_bytes)
        self.max_video_duration_seconds = int(max_video_duration_seconds)
        self.transport = transport
        if not self.api_key or "\n" in self.api_key or "\r" in self.api_key:
            raise PexelsError("PEXELS_CONFIG_INVALID", "PEXELS_API_KEY is required")
        if not 1 <= self.search_limit <= 15:
            raise PexelsError("PEXELS_CONFIG_INVALID", "Pexels search limit must be between 1 and 15")
        if not 1 <= self.timeout <= 120 or not 0 <= self.max_retries <= 3:
            raise PexelsError("PEXELS_CONFIG_INVALID", "Pexels timeout or retry count is invalid")
        if not 1_000_000 <= self.max_bytes <= 250 * 1024 * 1024:
            raise PexelsError("PEXELS_CONFIG_INVALID", "Pexels download byte limit is invalid")
        if not 1 <= self.max_video_duration_seconds <= 300:
            raise PexelsError("PEXELS_CONFIG_INVALID", "Pexels video duration limit is invalid")

    @classmethod
    def from_config(
        cls,
        config: Any,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> "PexelsClient":
        if not pexels_enabled(config):
            raise PexelsError("PEXELS_DISABLED", "Pexels stock sourcing is disabled")
        pexels_license_review_date(config)
        return cls(
            api_key=os.getenv("PEXELS_API_KEY", ""),
            search_limit=_env_int(
                "OPENSTORYLINE_PEXELS_SEARCH_LIMIT",
                getattr(config, "pexels_search_limit", 8),
            ),
            timeout=_env_float(
                "OPENSTORYLINE_PEXELS_TIMEOUT",
                getattr(config, "pexels_timeout", 30.0),
            ),
            max_retries=_env_int(
                "OPENSTORYLINE_PEXELS_MAX_RETRIES",
                getattr(config, "pexels_max_retries", 2),
            ),
            max_bytes=_env_int(
                "OPENSTORYLINE_PEXELS_MAX_BYTES",
                getattr(config, "pexels_max_bytes", 80 * 1024 * 1024),
            ),
            max_video_duration_seconds=_env_int(
                "OPENSTORYLINE_PEXELS_MAX_VIDEO_DURATION_SECONDS",
                getattr(config, "pexels_max_video_duration_seconds", 60),
            ),
            transport=transport,
        )

    async def _request_json(
        self,
        client: httpx.AsyncClient,
        *,
        path: str,
        params: dict[str, Any],
        attempts: list[PexelsAttempt],
    ) -> dict[str, Any]:
        url = f"{PEXELS_API_BASE_URL}{path}"
        headers = {
            "Authorization": self.api_key,
            "Accept": "application/json",
            "User-Agent": "FireRed-OpenStoryline/remote-mvp",
        }
        for number in range(1, self.max_retries + 2):
            try:
                async with client.stream("GET", url, headers=headers, params=params) as response:
                    chunks: list[bytes] = []
                    size = 0
                    limit = 2 * 1024 * 1024 if response.status_code < 400 else 16 * 1024
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > limit:
                            raise PexelsError(
                                "PEXELS_RESPONSE_TOO_LARGE",
                                "Pexels search response is too large",
                                attempts=attempts,
                            )
                        chunks.append(chunk)
                    content = b"".join(chunks)
            except httpx.HTTPError as exc:
                attempts.append(
                    PexelsAttempt(number, "search", None, _transport_reason(exc))
                )
                continue
            if response.status_code >= 400:
                attempts.append(PexelsAttempt(
                    number,
                    "search",
                    response.status_code,
                    "provider_http_error",
                ))
                if response.status_code < 500 and response.status_code != 429:
                    break
                continue
            try:
                payload = json.loads(content)
            except (UnicodeDecodeError, ValueError):
                attempts.append(PexelsAttempt(number, "search", response.status_code, "invalid JSON"))
                continue
            if not isinstance(payload, dict):
                attempts.append(PexelsAttempt(number, "search", response.status_code, "invalid JSON root"))
                continue
            attempts.append(PexelsAttempt(number, "search", response.status_code, "ok"))
            return payload
        raise PexelsError(
            "PEXELS_SEARCH_FAILED",
            "Pexels search failed without a provider fallback",
            attempts=attempts,
        )

    async def _download(
        self,
        client: httpx.AsyncClient,
        *,
        url: str,
        kind: str,
        attempts: list[PexelsAttempt],
    ) -> tuple[bytes, str, str]:
        current = _safe_url(
            url,
            allowed_hosts=PEXELS_DOWNLOAD_HOSTS,
            code="PEXELS_DOWNLOAD_URL_INVALID",
        )
        for redirect_count in range(3):
            try:
                async with client.stream(
                    "GET",
                    current,
                    headers={"User-Agent": "FireRed-OpenStoryline/remote-mvp"},
                    follow_redirects=False,
                ) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location", "")
                        current = _safe_url(
                            urljoin(current, location),
                            allowed_hosts=PEXELS_DOWNLOAD_HOSTS,
                            code="PEXELS_DOWNLOAD_REDIRECT_INVALID",
                        )
                        attempts.append(PexelsAttempt(
                            redirect_count + 1,
                            "download_redirect",
                            response.status_code,
                            "validated redirect",
                        ))
                        continue
                    if response.status_code >= 400:
                        attempts.append(PexelsAttempt(
                            redirect_count + 1,
                            "download",
                            response.status_code,
                            "download failed",
                        ))
                        raise PexelsError(
                            "PEXELS_DOWNLOAD_FAILED",
                            "Pexels media download failed",
                            attempts=attempts,
                        )
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                    allowed_types = (
                        {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
                        if kind == "stock_image"
                        else {"video/mp4": "mp4"}
                    )
                    extension = allowed_types.get(content_type)
                    if extension is None:
                        raise PexelsError(
                            "PEXELS_MEDIA_TYPE_INVALID",
                            "Pexels returned an unsupported media type",
                        )
                    length = response.headers.get("content-length")
                    if length:
                        try:
                            declared_size = int(length)
                        except ValueError as exc:
                            raise PexelsError(
                                "PEXELS_MEDIA_INVALID",
                                "Pexels returned an invalid content length",
                            ) from exc
                        if declared_size <= 0 or declared_size > self.max_bytes:
                            raise PexelsError(
                                "PEXELS_MEDIA_TOO_LARGE",
                                "Pexels media exceeds the byte limit",
                            )
                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > self.max_bytes:
                            raise PexelsError("PEXELS_MEDIA_TOO_LARGE", "Pexels media exceeds the byte limit")
                        chunks.append(chunk)
                    content = b"".join(chunks)
            except PexelsError:
                raise
            except (httpx.HTTPError, ValueError) as exc:
                attempts.append(PexelsAttempt(
                    redirect_count + 1,
                    "download",
                    None,
                    _transport_reason(exc),
                ))
                raise PexelsError(
                    "PEXELS_DOWNLOAD_FAILED",
                    "Pexels media download failed",
                    attempts=attempts,
                ) from exc
            if not content:
                raise PexelsError("PEXELS_MEDIA_INVALID", "Pexels returned empty media")
            if kind == "stock_image" and not (
                content.startswith(b"\xff\xd8\xff")
                or content.startswith(b"\x89PNG\r\n\x1a\n")
                or content.startswith(b"RIFF") and content[8:12] == b"WEBP"
            ):
                raise PexelsError("PEXELS_MEDIA_INVALID", "Pexels image bytes are invalid")
            if kind == "stock_video" and b"ftyp" not in content[:32]:
                raise PexelsError("PEXELS_MEDIA_INVALID", "Pexels video bytes are invalid")
            attempts.append(PexelsAttempt(redirect_count + 1, "download", 200, "ok"))
            return content, content_type, extension
        raise PexelsError(
            "PEXELS_REDIRECT_LIMIT_EXCEEDED",
            "Pexels download exceeded the redirect limit",
            attempts=attempts,
        )

    @staticmethod
    def _orientation_matches(width: int, height: int, orientation: str) -> bool:
        return height >= width if orientation == "portrait" else width >= height

    def _photo_candidate(self, payload: dict[str, Any], request: AssetRequest) -> dict[str, Any]:
        photos = payload.get("photos")
        if not isinstance(photos, list) or len(photos) > self.search_limit:
            raise PexelsError("PEXELS_RESPONSE_INVALID", "Pexels photo results are invalid")
        for photo in photos:
            if not isinstance(photo, dict):
                continue
            try:
                width = _positive_int(photo.get("width"), maximum=20_000, code="PEXELS_RESPONSE_INVALID")
                height = _positive_int(photo.get("height"), maximum=20_000, code="PEXELS_RESPONSE_INVALID")
                if not self._orientation_matches(width, height, request.orientation):
                    continue
                source = photo.get("src") or {}
                media_url = next(
                    value
                    for key in ("large2x", "large", "portrait", "landscape", "original")
                    if (value := source.get(key))
                )
                return {
                    "provider_id": _positive_int(photo.get("id"), maximum=2_147_483_647, code="PEXELS_RESPONSE_INVALID"),
                    "creator": _required_text(photo.get("photographer"), limit=160, code="PEXELS_RESPONSE_INVALID"),
                    "creator_url": _safe_url(photo.get("photographer_url"), allowed_hosts=PEXELS_SOURCE_HOSTS, code="PEXELS_RESPONSE_INVALID"),
                    "source_url": _safe_url(photo.get("url"), allowed_hosts=PEXELS_SOURCE_HOSTS, code="PEXELS_RESPONSE_INVALID"),
                    "media_url": _safe_url(media_url, allowed_hosts=PEXELS_DOWNLOAD_HOSTS, code="PEXELS_DOWNLOAD_URL_INVALID"),
                    "width": width,
                    "height": height,
                    "duration_seconds": None,
                }
            except (PexelsError, StopIteration):
                continue
        raise PexelsError("PEXELS_NO_COMPATIBLE_MEDIA", "Pexels returned no compatible photo")

    def _video_candidate(self, payload: dict[str, Any], request: AssetRequest) -> dict[str, Any]:
        videos = payload.get("videos")
        if not isinstance(videos, list) or len(videos) > self.search_limit:
            raise PexelsError("PEXELS_RESPONSE_INVALID", "Pexels video results are invalid")
        required_seconds = request.timeline_window.duration_ms / 1000
        for video in videos:
            if not isinstance(video, dict):
                continue
            try:
                duration = float(video.get("duration"))
                width = _positive_int(video.get("width"), maximum=20_000, code="PEXELS_RESPONSE_INVALID")
                height = _positive_int(video.get("height"), maximum=20_000, code="PEXELS_RESPONSE_INVALID")
                if (
                    not required_seconds <= duration <= self.max_video_duration_seconds
                    or not self._orientation_matches(width, height, request.orientation)
                ):
                    continue
                files = []
                for item in video.get("video_files") or []:
                    if not isinstance(item, dict) or item.get("file_type") != "video/mp4":
                        continue
                    file_width = _positive_int(item.get("width"), maximum=7680, code="PEXELS_RESPONSE_INVALID")
                    file_height = _positive_int(item.get("height"), maximum=7680, code="PEXELS_RESPONSE_INVALID")
                    if max(file_width, file_height) > 2160:
                        continue
                    files.append((abs(max(file_width, file_height) - 1080), item, file_width, file_height))
                if not files:
                    continue
                _score, selected, file_width, file_height = sorted(files, key=lambda item: item[0])[0]
                user = video.get("user") or {}
                return {
                    "provider_id": _positive_int(video.get("id"), maximum=2_147_483_647, code="PEXELS_RESPONSE_INVALID"),
                    "creator": _required_text(user.get("name"), limit=160, code="PEXELS_RESPONSE_INVALID"),
                    "creator_url": _safe_url(user.get("url"), allowed_hosts=PEXELS_SOURCE_HOSTS, code="PEXELS_RESPONSE_INVALID"),
                    "source_url": _safe_url(video.get("url"), allowed_hosts=PEXELS_SOURCE_HOSTS, code="PEXELS_RESPONSE_INVALID"),
                    "media_url": _safe_url(selected.get("link"), allowed_hosts=PEXELS_DOWNLOAD_HOSTS, code="PEXELS_DOWNLOAD_URL_INVALID"),
                    "width": file_width,
                    "height": file_height,
                    "duration_seconds": round(duration, 3),
                }
            except (PexelsError, TypeError, ValueError):
                continue
        raise PexelsError("PEXELS_NO_COMPATIBLE_MEDIA", "Pexels returned no compatible video")

    async def acquire(self, request: AssetRequest) -> PexelsAsset:
        if request.provider != "pexels" or request.kind not in {"stock_image", "stock_video"}:
            raise PexelsError("PEXELS_REQUEST_INVALID", "Pexels received an unsupported asset request")
        query = _clean(request.prompt, limit=240)
        if not query:
            raise PexelsError("PEXELS_REQUEST_INVALID", "Pexels search requests require a query")
        attempts: list[PexelsAttempt] = []
        path = "/v1/search" if request.kind == "stock_image" else "/videos/search"
        params = {
            "query": query,
            "orientation": request.orientation,
            "per_page": self.search_limit,
            "page": 1,
        }
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout),
            transport=self.transport,
            trust_env=False,
        ) as client:
            payload = await self._request_json(
                client,
                path=path,
                params=params,
                attempts=attempts,
            )
            candidate = (
                self._photo_candidate(payload, request)
                if request.kind == "stock_image"
                else self._video_candidate(payload, request)
            )
            content, content_type, extension = await self._download(
                client,
                url=candidate["media_url"],
                kind=request.kind,
                attempts=attempts,
            )
        return PexelsAsset(
            **candidate,
            kind=request.kind,
            content=content,
            extension=extension,
            content_type=content_type,
            file_size=len(content),
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            attempts=tuple(attempts),
        )
