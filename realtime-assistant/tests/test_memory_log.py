import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from unittest import mock

from test_app import app, FakeSpeaker, FakeWebSocket


class MemoryLogTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "logs" / "memory.jsonl"
        self.log = app.SessionMemoryLog(self.path, 1048576, 3)
        self.state = app.RuntimeState()
        self.client = app.RealtimeClient(
            app.Config.from_env(), FakeSpeaker(), self.state, memory_log=self.log
        )
        self.ws = FakeWebSocket()
        self.client._ws = self.ws

    def records(self):
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines()]

    def confirm(self, instructions=None):
        session = dict(json.loads(self.ws.sent[0])["session"], id="sess_test")
        if instructions is not None:
            session["instructions"] = instructions
        self.client._handle_session_updated({"type": "session.updated", "session": session})

    def test_logs_exact_injected_memories_and_one_confirmation_only(self):
        self.client._handoff_memory = '正在寻找“红球”。\n下一步：查看桌下。'
        self.client._unplanned_reconnect_pending = True
        self.client._next_session_reason = "unexpected_reconnect"
        reconnect = '用户: 找到了吗？\n助手: 还没有。'
        with mock.patch.object(self.client, "_recent_dialogue_memory", return_value=reconnect):
            self.client._on_open(self.ws)
        record = self.records()[0]
        self.assertEqual(record["handoff_memory"], self.client._handoff_memory)
        self.assertEqual(record["reconnect_memory"], reconnect)
        self.assertEqual(record["reason"], "unexpected_reconnect")
        self.assertEqual(record["status"], "sent")
        self.assertIsNotNone(datetime.fromisoformat(record["at"]).tzinfo)
        sent = json.loads(self.ws.sent[0])
        self.assertEqual(record["injection_id"], sent["event_id"])
        self.assertTrue(sent["session"]["instructions"].endswith(reconnect))
        self.assertIn(record["handoff_memory"], sent["session"]["instructions"])
        self.assertNotIn("instructions", record)
        self.assertNotIn("api_key", record)
        self.confirm()
        self.confirm()
        for _ in range(10):
            self.client.append_audio(b"audio")
            self.client.send({"type": "response.create"})
        records = self.records()
        self.assertEqual(len(records), 2)
        self.assertEqual(records[1]["status"], "confirmed")
        self.assertEqual(records[1]["session_id"], "sess_test")
        self.assertEqual(records[1]["injection_id"], record["injection_id"])
        self.assertNotIn("handoff_memory", records[1])

    def test_startup_without_memory_is_explicit_and_small(self):
        self.client._on_open(self.ws)
        record = self.records()[0]
        self.assertEqual(record["reason"], "startup")
        self.assertEqual(record["handoff_memory"], "")
        self.assertEqual(record["reconnect_memory"], "")
        self.assertLess(self.path.stat().st_size, 400)

    def test_send_failure_is_not_marked_sent_or_confirmed(self):
        with mock.patch.object(self.ws, "send", side_effect=OSError("offline")):
            self.client._on_open(self.ws)
        self.assertEqual(self.records()[0]["status"], "send_failed")
        self.assertIsNone(self.client._pending_memory_confirmation)

    def test_close_without_confirmation_and_next_session_reason(self):
        self.client._on_open(self.ws)
        self.client._on_close(self.ws, 1006, "offline")
        self.assertEqual(self.records()[-1]["status"], "unconfirmed")
        self.client._on_open(self.ws)
        self.assertEqual(self.records()[-1]["reason"], "unexpected_reconnect")
        self.client._planned_disconnect = True
        self.client._on_close(self.ws, 1000, "rotate")
        self.client._on_open(self.ws)
        self.assertEqual(self.records()[-1]["reason"], "session_rotation")

    def test_mismatched_or_rejected_update_cannot_confirm_memory(self):
        self.client._on_open(self.ws)
        self.confirm("different instructions")
        self.assertEqual(self.records()[-1]["status"], "instructions_mismatch")
        self.client._on_open(self.ws)
        event_id = self.records()[-1]["injection_id"]
        self.client._handle_realtime_error({"event_id": "other", "code": "invalid_value"})
        self.assertEqual(self.records()[-1]["event"], "memory_injection")
        self.client._handle_realtime_error({"event_id": event_id, "code": "invalid_value"})
        self.assertEqual(self.records()[-1]["status"], "rejected")
        self.assertIsNone(self.client._pending_memory_confirmation)

    def test_log_write_failure_does_not_break_session_and_is_visible(self):
        with mock.patch.object(self.log, "append", side_effect=OSError("disk full")):
            with self.assertLogs(app.LOG, level="ERROR"):
                self.client._on_open(self.ws)
        self.assertEqual(len(self.ws.sent), 1)
        self.assertEqual(self.state.snapshot()["memory_log_last_error"], "disk full")
        self.confirm()
        self.assertIsNone(self.state.snapshot()["memory_log_last_error"])
        self.assertEqual(self.state.snapshot()["memory_log_records_written"], 1)

    def test_utf8_rotation_bounded_and_survives_new_store(self):
        log = app.SessionMemoryLog(self.path, 300, 3)
        for index in range(20):
            log.append({"index": index, "handoff_memory": "中文语音" * 9})
        # A recreated process appends and rotates the same files.
        log = app.SessionMemoryLog(self.path, 300, 3)
        log.append({"index": 20, "handoff_memory": "中文语音" * 9})
        files = list(self.path.parent.iterdir())
        self.assertEqual(len(files), 4)
        self.assertTrue(all(path.stat().st_size <= 300 for path in files))
        self.assertEqual(self.records()[0]["index"], 20)
        oldest = json.loads(Path(f"{self.path}.3").read_text(encoding="utf-8"))
        self.assertEqual(oldest["index"], 17)

    def test_oversize_record_does_not_break_bound_or_erase_existing(self):
        log = app.SessionMemoryLog(self.path, 300, 3)
        log.append({"message": "keep"})
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "exceeds"):
            log.append({"message": "中文" * 300})
        self.assertEqual(before, self.path.read_bytes())

    def test_memory_size_limit_fits_maximum_escaped_contents(self):
        log = app.SessionMemoryLog(self.path, 262144, 3)
        log.append({"handoff_memory": "\x00" * 16000, "reconnect_memory": "\x01" * 16000})
        self.assertLess(self.path.stat().st_size, 262144)
        self.assertEqual(len(self.records()[0]["handoff_memory"]), 16000)

    def test_config_rejects_unbounded_or_too_small_logs(self):
        config = replace(app.Config.from_env(), openai_api_key="test", ebo_api_token="test")
        config.validate()
        for changes in (
            {"memory_log_path": ""}, {"memory_log_max_bytes": 0},
            {"memory_log_max_bytes": 1024}, {"memory_log_backup_count": 0},
            {"memory_log_backup_count": 21},
        ):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "EBO_MEMORY_LOG"):
                replace(config, **changes).validate()


if __name__ == "__main__":
    unittest.main()
