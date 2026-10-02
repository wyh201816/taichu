"""独立 Zstd 帧追加 UTF-8 JSONL；此前完整帧不受损坏尾部影响。"""

from __future__ import annotations

import asyncio
import hashlib
import os
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from collections.abc import Iterator

import zstandard

from taichu.application.trajectory.models import TrajectoryEvent, TrajectoryRead


class ZstdTrajectoryRepository:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._lock = RLock()
        self._positions: dict[str, tuple[tuple[tuple[str, int, int], ...], int, Path]] = {}

    def path_for(self, conversation_id: str) -> Path:
        filename = hashlib.sha256(conversation_id.encode("utf-8")).hexdigest()
        return self.root / f"{filename}.jsonl.zst"

    async def append(self, events: list[TrajectoryEvent]) -> None:
        if events:
            await asyncio.to_thread(self._append, events)

    def _append(self, events: list[TrajectoryEvent]) -> None:
        conversations = {event.conversation_id for event in events}
        if len(conversations) != 1:
            raise ValueError("一个追加批次只能包含同一会话的事件。")
        conversation_id = events[0].conversation_id
        with self._lock, self._coordinate(conversation_id):
            paths = self._paths(conversation_id)
            signature = self._signature(paths)
            cached = self._positions.get(conversation_id)
            if cached is not None and cached[0] == signature:
                _, sequence, path = cached
            else:
                prior, last_complete = self._read_frames(conversation_id, paths)
                sequence = max((event.sequence for event in prior.events), default=0)
                path = paths[-1] if paths and last_complete else self.path_for(conversation_id)
                if paths and not last_complete:
                    # 留下损坏文件作为完整性证据，新帧写入新的顺序分段。
                    path = self.path_for(conversation_id).with_name(
                        self.path_for(conversation_id).stem + f".part-{len(paths):08d}.zst")
            lines = []
            for event in events:
                sequence += 1
                lines.append(event.model_copy(update={"sequence": sequence}).model_dump_json())
            payload = ("\n".join(lines) + "\n").encode("utf-8")
            frame = zstandard.ZstdCompressor(write_checksum=True).compress(payload)
            self.root.mkdir(parents=True, exist_ok=True)
            with path.open("ab") as output:
                output.write(frame)
                output.flush()
                os.fsync(output.fileno())
            self._positions[conversation_id] = (self._signature(self._paths(conversation_id)), sequence, path)

    def _paths(self, conversation_id: str) -> list[Path]:
        base = self.path_for(conversation_id)
        return ([base] if base.exists() else []) + sorted(self.root.glob(base.stem + ".part-*.zst"))

    @staticmethod
    def _signature(paths: list[Path]) -> tuple[tuple[str, int, int], ...]:
        return tuple((path.name, path.stat().st_size, path.stat().st_mtime_ns) for path in paths)

    @contextmanager
    def _coordinate(self, conversation_id: str) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        with self.path_for(conversation_id).with_suffix(".lock").open("a+b") as lock:
            if lock.tell() == 0:
                lock.write(b"0")
                lock.flush()
            lock.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                getattr(fcntl, "flock")(lock.fileno(), getattr(fcntl, "LOCK_EX"))
            try:
                yield
            finally:
                lock.seek(0)
                if os.name == "nt":
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    getattr(fcntl, "flock")(lock.fileno(), getattr(fcntl, "LOCK_UN"))

    async def read(self, conversation_id: str) -> TrajectoryRead:
        return await asyncio.to_thread(self._read_locked, conversation_id)

    def _read_locked(self, conversation_id: str) -> TrajectoryRead:
        if not self.root.exists():
            return TrajectoryRead()
        with self._lock, self._coordinate(conversation_id):
            return self._read(conversation_id)

    def _read(self, conversation_id: str) -> TrajectoryRead:
        result, _ = self._read_frames(conversation_id, self._paths(conversation_id))
        return result

    def _read_frames(self, conversation_id: str, paths: list[Path]) -> tuple[TrajectoryRead, bool]:
        events: list[TrajectoryEvent] = []
        complete = True
        last_complete = True
        for path in paths:
            last_complete = True
            try:
                reader = zstandard.ZstdDecompressor().decompressobj()
                decoded = bytearray()
                frame_started = False
                with path.open("rb") as source:
                    while block := source.read(64 * 1024):
                        while block:
                            frame_started = True
                            decoded.extend(reader.decompress(block))
                            if not reader.eof:
                                break
                            batch = [TrajectoryEvent.model_validate_json(line)
                                     for line in decoded.decode("utf-8").splitlines() if line]
                            sequence = events[-1].sequence if events else 0
                            for event in batch:
                                if event.conversation_id != conversation_id or event.schema_version != 1 or event.sequence <= sequence:
                                    raise ValueError("会话、协议版本或事件顺序不一致")
                                sequence = event.sequence
                            events.extend(batch)
                            block = reader.unused_data
                            reader = zstandard.ZstdDecompressor().decompressobj()
                            decoded.clear()
                            frame_started = False
                    if frame_started:
                        raise ValueError("未完成的压缩帧")
            except (zstandard.ZstdError, ValueError, UnicodeError):
                complete = False
                last_complete = False
        if any(event.kind == "recording_gap" for event in events):
            complete = False
        return TrajectoryRead(events=events, complete=complete,
                              warnings=[] if complete else ["轨迹存在记录缺口或损坏分段；完整批次仍可读取。"]), last_complete
