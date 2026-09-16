import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from test_app import app, FakeSpeaker


class CloudEventTests(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"EBO_CLOUD": "1"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.state = app.RuntimeState()

    def test_transcript_and_reply_match_durable_records(self):
        transcripts = app.TranscriptStore(self.root / "transcripts.jsonl", self.state)
        outputs = app.AssistantOutputStore(self.root / "outputs.jsonl",
                                           app.AudioStore(self.root / "audio", self.state), self.state)
        with self.assertLogs(app.EVENT_LOG, level="INFO") as logs:
            transcripts.append("item_user", '你好\n"世界"', [{"code": "zh"}])
            outputs.append("resp_1", "resp_1", "item_reply", "你好，我在。")
        rows = [json.loads(r.getMessage()) for r in logs.records]
        self.assertEqual(rows[0]["event"], "conversation.user.transcript")
        self.assertEqual(rows[0]["transcript"], '你好\n"世界"')
        self.assertEqual(rows[1]["response_id"], "resp_1")
        self.assertTrue(all(r["persisted"] for r in rows))
        self.assertEqual(json.loads(outputs.path.read_text())["transcript"], rows[1]["transcript"])

    def test_long_unicode_reply_preserves_all_text_and_redacts_before_chunking(self):
        text = "文" * 3995 + "private-test-key" + "下一段\n" * 1800
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "private-test-key"}):
            with self.assertLogs(app.EVENT_LOG, level="INFO") as logs:
                app.cloud_event("conversation.assistant.output", transcript=text, response_id="r1")
        rows = [json.loads(r.getMessage()) for r in logs.records]
        self.assertEqual("".join(r["transcript"] for r in rows), text.replace("private-test-key", "[REDACTED]"))
        self.assertEqual(len({r["event_id"] for r in rows}), 1)
        self.assertEqual([r["chunk_index"] for r in rows], list(range(len(rows))))
        self.assertTrue(all(r["chunk_count"] == len(rows) for r in rows))
        self.assertTrue(all(len(r.getMessage().encode()) < 32000 for r in logs.records))

    def test_file_failure_still_exports_received_transcript(self):
        store = app.TranscriptStore(self.root / "transcripts.jsonl", self.state)
        client = app.RealtimeClient(app.Config.from_env(), FakeSpeaker(), self.state, transcripts=store)
        with mock.patch.object(store, "append", side_effect=OSError("disk full")):
            with self.assertLogs(app.EVENT_LOG, level="INFO") as logs:
                client._handle_input_transcript({"item_id": "i1", "transcript": "保存失败的转写"})
        row = json.loads(logs.records[0].getMessage())
        self.assertFalse(row["persisted"])
        self.assertEqual(row["transcript"], "保存失败的转写")
        self.assertEqual(row["severity"], "ERROR")

    def test_response_status_does_not_dump_full_api_payload(self):
        client = app.RealtimeClient(app.Config.from_env(), FakeSpeaker(), self.state)
        with self.assertLogs(app.EVENT_LOG, level="INFO") as logs:
            client._handle_response_done({"response": {
                "id": "r1", "status": "failed", "output": [{"secret": "not-for-logs"}],
                "usage": {"input_tokens": 12, "output_tokens": 3, "total_tokens": 15, "raw": "not-for-logs"},
                "status_details": {"error": {"code": "test_error", "message": "not-for-logs"}}}})
        row = json.loads(logs.records[0].getMessage())
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["total_tokens"], 15)
        self.assertNotIn("not-for-logs", logs.records[0].getMessage())

    def test_local_mode_does_not_export_conversation(self):
        with mock.patch.dict(os.environ, {"EBO_CLOUD": "0"}):
            with mock.patch.object(app.EVENT_LOG, "log") as log:
                app.cloud_event("conversation.user.transcript", transcript="local only")
        log.assert_not_called()
