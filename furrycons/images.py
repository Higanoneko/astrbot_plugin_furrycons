"""Reuse official API image resources for seven days, isolated from API credentials."""

import asyncio
import hashlib
import io
import os
import time
import warnings
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from PIL import Image, ImageOps

from .image_colors import edge_colors

TTL_SECONDS = 7 * 24 * 60 * 60
MAX_DOWNLOAD = 24 * 1024 * 1024
MAX_PIXELS = 40_000_000
MAX_CACHE = 256 * 1024 * 1024


def official_image_url(value):
    if not isinstance(value, str) or not value or len(value) > 4096:
        return None
    if any(c.isspace() or ord(c) < 32 or c == "\\" for c in value):
        return None
    try:
        url = urlsplit(value)
        if (
            url.scheme != "https"
            or url.netloc != "images.furrycons.cn"
            or url.username is not None
            or url.fragment
            or not url.path.startswith("/")
        ):
            return None
        return str(httpx.URL(value))
    except (ValueError, httpx.InvalidURL):
        return None


def image_reference(value):
    url = official_image_url(value)
    # The official API does not supply dimensions. Zero means unknown; the
    # browser lays out the decoded image using its intrinsic aspect ratio.
    return {"url": url, "width": 0, "height": 0} if url else None


def render_image_urls(view):
    """Only exact references supplied with this render may reach the CDN."""
    root = next(iter(view.values()))
    events = root.get("events", []) if "events" in root else [root]
    return {
        url
        for event in events
        for name in ("poster", "organizer_logo")
        if isinstance(reference := event.get(name), dict)
        if (url := official_image_url(reference.get("url")))
    }


@dataclass(frozen=True)
class ImageAsset:
    key: str
    data: bytes
    width: int
    height: int
    edge_colors: tuple[str, str, str]

    @property
    def url(self):
        return f"https://furrycons.invalid/media/{self.key}.png"

    def reference(self):
        return {
            "url": self.url, "width": self.width, "height": self.height,
            "edge_colors": dict(zip(("top", "middle", "bottom"), self.edge_colors, strict=True)),
        }


def _normalize(data):
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(data)) as original:
            if original.format not in ("JPEG", "PNG", "WEBP", "GIF"):
                raise ValueError("Unsupported image")
            if original.width * original.height > MAX_PIXELS:
                raise ValueError("Image too large")
            original.seek(0)
            original.load()
            image = ImageOps.exif_transpose(original).convert(
                "RGBA" if original.mode in ("RGBA", "LA", "P") else "RGB"
            )
        try:
            image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.save(output, format="PNG")
            return output.getvalue(), image.width, image.height, edge_colors(image)
        finally:
            image.close()


class ImageCache:
    def __init__(self, directory, *, transport=None, clock=time.time, max_bytes=MAX_CACHE):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.clock, self.max_bytes = clock, max_bytes
        self.client = httpx.AsyncClient(
            timeout=8, follow_redirects=False, transport=transport,
            headers={"Accept": "image/jpeg,image/png,image/webp,image/gif"},
        )
        self._network = asyncio.Semaphore(4)
        self._decode = asyncio.Semaphore(1)
        self._pending = {}

    def _read(self, key):
        path = self.directory / f"{key}.png"
        try:
            stat = path.stat()
            if not 0 <= self.clock() - stat.st_mtime < TTL_SECONDS:
                return None
            if stat.st_size > MAX_DOWNLOAD:
                return None
            data = path.read_bytes()
            with Image.open(io.BytesIO(data)) as image:
                if image.format != "PNG" or image.width * image.height > MAX_PIXELS:
                    return None
                width, height = image.size
                image.verify()
            with Image.open(io.BytesIO(data)) as image:
                palette = edge_colors(image)
            try:
                os.utime(path, (self.clock(), stat.st_mtime))
            except OSError:
                # Access time is only an eviction hint, not part of validity.
                pass
            return ImageAsset(key, data, width, height, palette)
        except (OSError, ValueError, Image.DecompressionBombError):
            return None

    def _prune(self):
        try:
            files = [(p, p.stat()) for p in self.directory.glob("*.png")]
            total = sum(stat.st_size for _, stat in files)
            for path, stat in sorted(files, key=lambda item: item[1].st_atime):
                if total <= self.max_bytes and 0 <= self.clock() - stat.st_mtime < TTL_SECONDS:
                    continue
                path.unlink(missing_ok=True)
                total -= stat.st_size
        except OSError:
            # Storage pressure must not turn a valid convention query into a failure.
            pass

    async def get(self, value):
        if not (url := official_image_url(value)):
            return None
        key = hashlib.sha256(("image-v1:" + url).encode()).hexdigest()
        if cached := await asyncio.to_thread(self._read, key):
            return cached
        if key not in self._pending:
            task = asyncio.create_task(self._fetch(url, key))
            self._pending[key] = task
            task.add_done_callback(lambda done: self._pending.pop(key, None))
        return await asyncio.shield(self._pending[key])

    async def _fetch(self, url, key):
        try:
            async with self._network:
                async with asyncio.timeout(8):
                    async with self.client.stream("GET", url) as response:
                        if response.status_code != 200:
                            return None
                        if response.headers.get("content-type", "").split(";")[0].lower() not in (
                            "image/jpeg", "image/png", "image/webp", "image/gif",
                        ):
                            return None
                        length = response.headers.get("content-length")
                        if length and (not length.isdigit() or int(length) > MAX_DOWNLOAD):
                            return None
                        data = bytearray()
                        async for chunk in response.aiter_bytes():
                            data.extend(chunk)
                            if len(data) > MAX_DOWNLOAD:
                                return None
                async with self._decode:
                    normalized, width, height, palette = await asyncio.to_thread(_normalize, bytes(data))
                asset = ImageAsset(key, normalized, width, height, palette)
                try:
                    await asyncio.to_thread(self._save, asset)
                except OSError:
                    # A full or temporarily unavailable cache must not discard a
                    # successfully downloaded image for the current render.
                    pass
                return asset
        except (
            httpx.HTTPError, OSError, ValueError, TimeoutError,
            Image.DecompressionBombError, Image.DecompressionBombWarning,
        ):
            return None

    def _save(self, asset):
        temporary = self.directory / f".{uuid4().hex}.tmp"
        try:
            temporary.write_bytes(asset.data)
            os.utime(temporary, (self.clock(), self.clock()))
            temporary.replace(self.directory / f"{asset.key}.png")
            self._prune()
        finally:
            temporary.unlink(missing_ok=True)

    async def prepare(self, view, *, timeout=20):
        data = deepcopy(view)
        urls = render_image_urls(data)
        jobs = {url: asyncio.create_task(self.get(url)) for url in urls}
        if jobs:
            _, pending = await asyncio.wait(jobs.values(), timeout=timeout)
            for job in pending:
                job.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        assets = {
            url: job.result() for url, job in jobs.items()
            if not job.cancelled() and job.exception() is None and job.result() is not None
        }
        root = next(iter(data.values()))
        events = root.get("events", []) if "events" in root else [root]
        for event in events:
            event.setdefault("organization_name", "")
            for field in ("poster", "organizer_logo"):
                reference = event.get(field)
                original = reference.get("url") if isinstance(reference, dict) else None
                asset = assets.get(original)
                event[field] = asset.reference() if asset else None
                if field == "poster":
                    unavailable = (original or event.get("thumbnail")) and not asset
                    event["poster_note"] = "海报暂不可用" if unavailable else "暂无展会海报"
        return data, {asset.url: asset.data for asset in assets.values()}

    async def close(self):
        pending = list(self._pending.values())
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        await self.client.aclose()
