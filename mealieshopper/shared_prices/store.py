"""Atomic generations and process-safe lifetime locks without request writes."""

from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import os
import shutil
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO, TypeVar

import httpx

from mealieshopper import http_client, tasks

from .protocol import CHUNK_BYTES, MANIFEST_BYTES, RETAINED_BYTES, SHA256, strict_json, validate_dataset, validate_file, validate_manifest
from .reader import Snapshot

T = TypeVar("T")
REFRESH_SECONDS: int = 24 * 60 * 60
RETRY_SECONDS: int = 15 * 60
MANIFEST_PATH: str = "shared-prices/v1/manifest.json"
log: logging.Logger = logging.getLogger(__name__)


async def _thread(function: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    work: asyncio.Task[T] = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await tasks.share(work)
    except asyncio.CancelledError:
        try:
            await work
        except Exception:
            pass
        raise


class GenerationStore:
    """One trusted private directory, an atomic pointer, and advisory file locks."""

    def __init__(self, root: Path) -> None:
        self.root: Path = root

    def _prepare(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        for name in ("updater.lock", "selection.lock"):
            with (self.root / name).open("ab"):
                pass

    @contextmanager
    def _selection(self, exclusive: bool = False) -> Iterator[None]:
        with (self.root / "selection.lock").open("rb") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            yield

    def _pointer(self) -> dict[str, Any]:
        try:
            with (self.root / "active.json").open("rb") as handle:
                value: Any = strict_json(handle.read(MANIFEST_BYTES + 1))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def _ready(self, entry: Any, store: str) -> bool:
        if not isinstance(entry, dict) or entry.get("store") != store:
            return False
        try:
            validate_dataset(entry)
        except ValueError:
            return False
        artifact: Any = entry.get("artifact")
        if not isinstance(artifact, dict) or not isinstance(artifact.get("sha256"), str) or not SHA256.fullmatch(artifact["sha256"]):
            return False
        try:
            return (self.root / f"{artifact['sha256']}.sqlite").stat().st_size == artifact.get("bytes")
        except OSError:
            return False

    def _chosen(self, store: str) -> dict[str, Any] | None:
        pointer: dict[str, Any] = self._pointer()
        for key in ("active", "previous"):
            entry: Any = pointer.get(key)
            if self._ready(entry, store):
                return entry
        return None

    def generation(self, store: str = "ah") -> str | None:
        entry: dict[str, Any] | None = self._chosen(store)
        return entry["artifact"]["sha256"] if entry else None

    @contextmanager
    def lease(self, store: str = "ah") -> Iterator[Snapshot | None]:
        if not (self.root / "selection.lock").exists():
            yield None
            return
        handle: BinaryIO | None = None
        try:
            with self._selection():
                entry: dict[str, Any] | None = self._chosen(store)
                if entry is not None:
                    digest: str = entry["artifact"]["sha256"]
                    path: Path = self.root / f"{digest}.sqlite"
                    handle = path.open("rb")
                    fcntl.flock(handle, fcntl.LOCK_SH)
            yield Snapshot(path, digest, store) if handle is not None else None
        finally:
            if handle is not None:
                handle.close()

    def _write_json(self, name: str, value: dict[str, Any]) -> None:
        descriptor: int
        filename: str
        descriptor, filename = tempfile.mkstemp(prefix=f".{name}-", dir=self.root)
        try:
            with os.fdopen(descriptor, "w") as handle:
                json.dump(value, handle, separators=(",", ":"), allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(filename, self.root / name)
            directory: int = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            Path(filename).unlink(missing_ok=True)

    def _claim(self) -> BinaryIO | None:
        self._prepare()
        handle: BinaryIO = (self.root / "updater.lock").open("rb")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            return None
        return handle

    def install(self, candidate: Path, descriptor: dict[str, Any]) -> None:
        """Validate and copy an external candidate, retaining active and previous files."""
        lock: BinaryIO | None = self._claim()
        if lock is None:
            raise ValueError("Snapshot updater already running")
        try:
            self._install(candidate, descriptor)
        finally:
            lock.close()

    def _space(self, required: int, allocated: bool = False) -> None:
        total: int = sum(path.stat().st_size for path in self.root.glob("*.sqlite"))
        needed: int = 0 if allocated else required
        if total + required > RETAINED_BYTES or shutil.disk_usage(self.root).free < needed + CHUNK_BYTES:
            raise ValueError("Insufficient space for a new snapshot")

    def _install(self, candidate: Path, descriptor: dict[str, Any]) -> None:
        validate_dataset(descriptor)
        validate_file(candidate, descriptor)
        self.cleanup()
        digest: str = descriptor["artifact"]["sha256"]
        destination: Path = self.root / f"{digest}.sqlite"
        downloaded: bool = candidate == self.root / "candidate.download"
        staging: Path = candidate if downloaded else self.root / "candidate.install"
        if not downloaded:
            staging.unlink(missing_ok=True)
        try:
            self._space(descriptor["artifact"]["bytes"], downloaded)
            if downloaded:
                with staging.open("rb") as source:
                    os.fsync(source.fileno())
            else:
                with candidate.open("rb") as source, staging.open("wb") as target:
                    shutil.copyfileobj(source, target, CHUNK_BYTES)
                    target.flush()
                    os.fsync(target.fileno())
                validate_file(staging, descriptor)
            os.chmod(staging, 0o400)
            with self._selection(exclusive=True):
                if not destination.exists():
                    os.replace(staging, destination)
                else:
                    validate_file(destination, descriptor)
                old: dict[str, Any] = self._pointer()
                previous: Any = old.get("active")
                if previous and previous["artifact"]["sha256"] == digest:
                    previous = old.get("previous")
                self._write_json("active.json", {"active": descriptor, "previous": previous})
        finally:
            staging.unlink(missing_ok=True)
        self.cleanup()

    def cleanup(self) -> list[str]:
        """Remove retired files only after taking their exclusive lifetime lock."""
        if not self.root.exists():
            return []
        removed: list[str] = []
        with self._selection(exclusive=True):
            pointer: dict[str, Any] = self._pointer()
            keep: set[str] = {entry["artifact"]["sha256"] for key in ("active", "previous") if isinstance(entry := pointer.get(key), dict)}
            for path in self.root.glob("*.sqlite"):
                if path.stem in keep:
                    continue
                with path.open("rb") as handle:
                    try:
                        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        continue
                    path.unlink()
                    removed.append(path.name)
        return removed

    def _due(self, force: bool) -> bool:
        if force:
            return True
        try:
            with (self.root / "schedule.json").open("rb") as handle:
                schedule: Any = strict_json(handle.read(1024), 1024)
            return float(schedule["next_check"]) <= time.time()
        except (OSError, ValueError, KeyError, TypeError):
            return True

    async def refresh_if_due(self, force: bool = False) -> dict[str, Any]:
        if os.environ.get("MEALIESHOPPER_SHARED_PRICES", "true").strip().lower() in {"0", "false", "no", "off"}:
            return {"state": "disabled", "changed": False}
        claim: asyncio.Task[BinaryIO | None] = asyncio.create_task(asyncio.to_thread(self._claim))
        try:
            lock: BinaryIO | None = await tasks.share(claim)
        except OSError as exc:
            return {"state": "error", "changed": False, "error": str(exc)}
        except asyncio.CancelledError:
            abandoned: BinaryIO | None = await claim
            if abandoned is not None:
                abandoned.close()
            raise
        if lock is None:
            return {"state": "busy", "changed": False}
        try:
            if not await _thread(self._due, force):
                return {"state": "not_due", "changed": False}
            result: dict[str, Any] = await self._refresh()
            await _thread(self._schedule, REFRESH_SECONDS)
            return result
        except asyncio.CancelledError:
            raise
        except (OSError, ValueError, httpx.HTTPError) as exc:
            await _thread(self._schedule, RETRY_SECONDS)
            return {"state": "error", "changed": False, "error": str(exc)}
        finally:
            await _thread(lock.close)

    def _schedule(self, seconds: int) -> None:
        try:
            self._write_json("schedule.json", {"next_check": time.time() + seconds})
        except OSError:
            log.warning("Could not persist shared snapshot retry schedule", exc_info=True)

    async def _refresh(self) -> dict[str, Any]:
        from mealieshopper import price_archive

        client: httpx.AsyncClient = http_client.client("shared-snapshots", timeout=60.0, follow_redirects=False)
        manifest: dict[str, Any] = await self._manifest(client, price_archive.DATA_SITE_URL)
        for attempt in range(2):
            descriptor: dict[str, Any] = manifest["datasets"][0]
            if await _thread(self.generation) == descriptor["artifact"]["sha256"]:
                return {"state": "ready", "changed": False}
            await _thread(self.cleanup)
            await _thread(self._space, descriptor["artifact"]["bytes"])
            try:
                await self._download(client, price_archive.DATA_SITE_URL, descriptor)
                return {"state": "ready", "changed": True}
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 404 or attempt:
                    raise
                manifest = await self._manifest(client, price_archive.DATA_SITE_URL)
        raise ValueError("Snapshot disappeared")

    async def _manifest(self, client: httpx.AsyncClient, base: str) -> dict[str, Any]:
        body: bytearray = bytearray()
        async with client.stream("GET", f"{base}/{MANIFEST_PATH}", headers={"Accept-Encoding": "identity"}) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes(chunk_size=CHUNK_BYTES):
                if len(body) + len(chunk) > MANIFEST_BYTES:
                    raise ValueError("Manifest exceeds size limit")
                body.extend(chunk)
        return await _thread(validate_manifest, bytes(body))

    async def _download(self, client: httpx.AsyncClient, base: str, descriptor: dict[str, Any]) -> None:
        path: Path = self.root / "candidate.download"
        await _thread(path.unlink, missing_ok=True)
        handle: BinaryIO = await _thread(path.open, "wb")
        received: int = 0
        try:
            async with client.stream("GET", f"{base}/{descriptor['artifact']['path']}", headers={"Accept-Encoding": "identity"}) as response:
                response.raise_for_status()
                if response.headers.get("content-encoding", "identity") != "identity":
                    raise ValueError("Compressed snapshot transport is unsupported")
                async for chunk in response.aiter_bytes(chunk_size=CHUNK_BYTES):
                    received += len(chunk)
                    if received > descriptor["artifact"]["bytes"]:
                        raise ValueError("Snapshot download exceeds declared size")
                    await _thread(handle.write, chunk)
            await _thread(handle.close)
            await _thread(self._install, path, descriptor)
        finally:
            await _thread(handle.close)
            await _thread(path.unlink, missing_ok=True)
