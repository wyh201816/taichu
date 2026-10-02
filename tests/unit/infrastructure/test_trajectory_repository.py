"""观测存储公共契约：追加、并发、重启和损坏边界。"""

import asyncio
import tempfile
import unittest
from pathlib import Path

from taichu.application.trajectory.models import TrajectoryEvent
from taichu.infrastructure.trajectory.zstd_repository import ZstdTrajectoryRepository


class TrajectoryRepositoryTest(unittest.IsolatedAsyncioTestCase):
    async def test_long_actual_result_and_truncation_flag_survive_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            content = '中文正文\n引号"与制表符\t' * 5000
            event = TrajectoryEvent(conversation_id="合成长结果", run_id="合成运行", request_index=1,
                                    kind="tool_completed", call_id="合成工具调用",
                                    data={"result": {"content": content, "truncated": True}})
            await ZstdTrajectoryRepository(root).append([event])
            trace = await ZstdTrajectoryRepository(root).read("合成长结果")
            self.assertTrue(trace.complete)
            self.assertEqual(trace.events[0].data["result"], event.data["result"])

    async def test_concurrent_batches_restart_and_corrupt_tail_preserve_committed_records(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = ZstdTrajectoryRepository(root)
            events = [TrajectoryEvent(conversation_id="合成会话", run_id="合成运行", request_index=1,
                                      kind="user", data={"content": f"中文\n批次 {index}"}) for index in range(12)]
            second_writer = ZstdTrajectoryRepository(root)
            await asyncio.gather(*(writer.append([event]) for writer, event in
                                   zip([repository, second_writer] * 6, events, strict=True)))
            restarted = ZstdTrajectoryRepository(root)
            trace = await restarted.read("合成会话")
            self.assertTrue(trace.complete)
            self.assertEqual({event.event_id for event in trace.events}, {event.event_id for event in events})
            self.assertEqual([event.sequence for event in trace.events], list(range(1, 13)))
            with repository.path_for("合成会话").open("ab") as output:
                output.write(b"\x28\xb5\x2f\xfd\x00")
            damaged = await restarted.read("合成会话")
            self.assertFalse(damaged.complete)
            self.assertEqual(damaged.events, trace.events)
            following = TrajectoryEvent(conversation_id="合成会话", run_id="合成运行", request_index=2,
                                        kind="user", data={"content": "故障后继续"})
            await restarted.append([following])
            recovered = await ZstdTrajectoryRepository(root).read("合成会话")
            self.assertFalse(recovered.complete)
            self.assertEqual(len(recovered.events), 13)
            self.assertEqual(recovered.events[-1].sequence, 13)
            self.assertEqual(recovered.events[-1].data["content"], "故障后继续")
