import importlib.util
import json
import os
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

import numpy as np


MODULE_PATH = Path(__file__).parents[1] / "app.py"
SPEC = importlib.util.spec_from_file_location("ebo_realtime_app", MODULE_PATH)
assert SPEC and SPEC.loader
app = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = app
SPEC.loader.exec_module(app)


def frame_with_square(position=None):
    frame = np.zeros((180, 320, 3), dtype=np.uint8)
    if position:
        x, y = position
        frame[y : y + 40, x : x + 40] = 255
    return frame


def warm(gate):
    for index in range(7):
        gate.observe(frame_with_square(), now=float(index))


class FakeWebSocket:
    def __init__(self):
        self.sent = []
        self.closed = False

    def send(self, payload):
        self.sent.append(payload)

    def close(self):
        self.closed = True


class FakeSpeaker:
    def __init__(self):
        self.played = []
        self.deltas = []
        self.finished = []
        self.aborted = 0
        self.active_id = ""
        self.interrupt_result = {
            "streamed": True,
            "interrupted": True,
            "generated_ms": 400,
            "played_ms": 240,
            "stream_id": "stream_fake",
        }

    def play(self, pcm, output_id=""):
        self.played.append((pcm, output_id))

    def stream_delta(self, pcm, output_id=""):
        self.deltas.append((pcm, output_id))

    def finish_stream(self, pcm, output_id=""):
        self.finished.append((pcm, output_id))

    def abort_stream(self):
        self.aborted += 1

    def interrupt(self, _pcm, _output_id):
        return dict(self.interrupt_result)

    def output_metrics(self, _output_id):
        return {}

    def active_output_id(self):
        return self.active_id

    def echo_reference(self):
        return b"", 0


class AlwaysSpeech:
    def is_speech(self, _frame, _rate):
        return True


class EnergySpeech:
    def is_speech(self, frame, _rate):
        return bool(np.max(np.abs(np.frombuffer(frame, dtype="<i2"))) > 100)


class AssistantTests(unittest.TestCase):
    def test_builtin_voice_name_is_case_normalized(self):
        with mock.patch.dict(os.environ, {"OPENAI_REALTIME_VOICE": "Verse"}):
            self.assertEqual(app.Config.from_env().voice, "verse")

    def test_empty_instruction_env_uses_safe_default(self):
        previous = os.environ.get("EBO_ASSISTANT_INSTRUCTIONS")
        os.environ["EBO_ASSISTANT_INSTRUCTIONS"] = ""
        try:
            config = app.Config.from_env()
        finally:
            if previous is None:
                os.environ.pop("EBO_ASSISTANT_INSTRUCTIONS", None)
            else:
                os.environ["EBO_ASSISTANT_INSTRUCTIONS"] = previous
        self.assertIn("EBO", config.instructions)

    def test_motion_requires_confirmation_and_respects_cooldown(self):
        gate = app.MotionGate(confirm_frames=2, cooldown_seconds=10, warmup_frames=2)
        warm(gate)
        first = gate.observe(frame_with_square((30, 30)), now=20)
        second = gate.observe(frame_with_square((50, 30)), now=21)
        cooldown = gate.observe(frame_with_square((70, 30)), now=22)
        self.assertEqual(first.reason, "confirming")
        self.assertTrue(second.send)
        self.assertEqual(cooldown.reason, "cooldown")

    def test_global_scene_change_recalibrates_instead_of_sending(self):
        gate = app.MotionGate(max_change_ratio=0.4, warmup_frames=2)
        warm(gate)
        bright = np.full((180, 320, 3), 255, dtype=np.uint8)
        decision = gate.observe(bright, now=20)
        self.assertFalse(decision.send)
        self.assertEqual(decision.reason, "scene_reset")

    def test_audio_store_writes_24khz_mono_wav(self):
        state = app.RuntimeState()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            store = app.AudioStore(path, state)
            name, duration = store.write_pcm24_wav(
                b"\0\0" * 24000, "resp_test_123"
            )
            self.assertEqual(name, "reply-resp_test_123.wav")
            self.assertTrue((path / name).is_file())
            self.assertEqual(duration, 1.0)
            self.assertEqual(state.snapshot()["output_audio_files_persisted"], 1)

    def test_old_output_audio_is_not_automatically_deleted(self):
        state = app.RuntimeState()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            old = path / "reply-old.wav"
            old.write_bytes(b"old")
            os.utime(old, (1, 1))
            store = app.AudioStore(path, state)
            store.write_pcm24_wav(b"\0\0", "resp_new")
            self.assertTrue(old.is_file())

    def test_audio_delta_reaches_stream_before_output_done(self):
        config = app.Config.from_env()
        state = app.RuntimeState()
        speaker = FakeSpeaker()
        client = app.RealtimeClient(config, speaker, state)
        delta = b"\x01\x00" * 480
        client._on_message(
            FakeWebSocket(),
            json.dumps(
                {
                    "type": "response.output_audio.delta",
                    "response_id": "resp_stream",
                    "delta": __import__("base64").b64encode(delta).decode(),
                }
            ),
        )
        self.assertEqual(speaker.deltas, [(delta, "resp_stream")])
        self.assertEqual(speaker.finished, [])

        client._on_message(
            FakeWebSocket(),
            json.dumps(
                {
                    "type": "response.output_audio.done",
                    "response_id": "resp_stream",
                }
            ),
        )
        self.assertEqual(speaker.finished, [(delta, "resp_stream")])

    def test_stream_failure_falls_back_to_persistent_wav_url(self):
        config = app.Config.from_env()
        state = app.RuntimeState()

        class FakeEbo:
            def __init__(self):
                self.commands = []

            def command(self, suffix, payload=""):
                self.commands.append((suffix, payload))

        ebo = FakeEbo()
        pcm = b"\0\0" * 12000
        with tempfile.TemporaryDirectory() as directory:
            store = app.AudioStore(Path(directory), state)
            speaker = app.Speaker(config, ebo, store, state)
            with mock.patch.object(
                app.websocket,
                "create_connection",
                side_effect=OSError("stream unavailable"),
            ):
                speaker.stream_delta(pcm, "resp_fallback")
                speaker.finish_stream(pcm, "resp_fallback")
                deadline = time.monotonic() + 2
                while not ebo.commands and time.monotonic() < deadline:
                    time.sleep(0.01)

            self.assertTrue((Path(directory) / "reply-resp_fallback.wav").is_file())
            self.assertEqual(ebo.commands[0][0], "talk")
            self.assertIn("reply-resp_fallback.wav", ebo.commands[0][1])
            self.assertEqual(state.snapshot()["speaker_stream_fallbacks"], 1)

    def test_barge_gate_rejects_pure_echo(self):
        gate = app.BargeInGate(vad=AlwaysSpeech())
        samples = np.arange(24000 * 300 // 1000)
        pcm = (np.sin(samples * 2 * np.pi * 440 / 24000) * 9000).astype("<i2").tobytes()
        decision = gate.observe(pcm, pcm, 300)
        self.assertFalse(decision.triggered)
        self.assertGreater(decision.echo_correlation, 0.95)
        self.assertLess(decision.residual_ratio, 0.1)

    def test_barge_gate_rejects_short_noise(self):
        gate = app.BargeInGate(vad=AlwaysSpeech())
        pcm = (np.random.default_rng(4).normal(0, 3000, 2400)).astype("<i2").tobytes()
        decision = gate.observe(pcm, b"", 0)
        self.assertFalse(decision.triggered)
        self.assertEqual(decision.speech_ms, 100)

    def test_barge_gate_accepts_voice_residual_and_keeps_preroll_once(self):
        gate = app.BargeInGate(vad=AlwaysSpeech())
        count = 24000 * 300 // 1000
        samples = np.arange(count)
        echo = np.sin(samples * 2 * np.pi * 440 / 24000) * 5000
        independent = np.sin(samples * 2 * np.pi * 173 / 24000) * 9000
        reference = echo.astype("<i2").tobytes()
        microphone = (echo + independent).astype("<i2").tobytes()
        first = gate.observe(microphone, reference, 300)
        second = gate.observe(microphone, reference + reference, 600)
        self.assertTrue(first.triggered)
        self.assertGreaterEqual(first.residual_ratio, 0.45)
        self.assertEqual(len(first.preroll), len(microphone))
        self.assertFalse(second.triggered)

    def test_barge_gate_replays_full_500ms_opening_preroll(self):
        gate = app.BargeInGate(vad=EnergySpeech())
        quiet = bytes(24000 * 200 // 1000 * 2)
        samples = np.arange(24000 * 300 // 1000)
        voice = (np.sin(samples * 2 * np.pi * 173 / 24000) * 6000).astype("<i2").tobytes()
        decision = gate.observe(quiet + voice, b"", 0)
        self.assertTrue(decision.triggered)
        self.assertEqual(len(decision.preroll), 24000 * 500 // 1000 * 2)
        self.assertEqual(decision.preroll[: len(quiet)], quiet)

    def test_barge_in_sends_cancel_truncate_then_preroll(self):
        config = replace(app.Config.from_env(), barge_in_enabled=True)
        state = app.RuntimeState()
        speaker = FakeSpeaker()
        client = app.RealtimeClient(config, speaker, state)
        ws = FakeWebSocket()
        client._ws = ws
        state.update(realtime_connected=True)
        client._normal_response_active = True
        client._active_response_id = "resp_interrupt"
        client._active_output_item_id = "item_interrupt"
        client._active_output_content_index = 2
        client._response_audio.extend(b"\0\0" * 9600)
        decision = app.BargeInDecision(True, b"preroll", 220, 0.2, 0.7)

        client.handle_barge_in(decision)

        events = [json.loads(payload) for payload in ws.sent]
        self.assertEqual(
            [event["type"] for event in events],
            [
                "response.cancel",
                "conversation.item.truncate",
                "input_audio_buffer.append",
            ],
        )
        self.assertEqual(events[1]["item_id"], "item_interrupt")
        self.assertEqual(events[1]["content_index"], 2)
        self.assertEqual(events[1]["audio_end_ms"], 240)
        self.assertEqual(__import__("base64").b64decode(events[2]["audio"]), b"preroll")
        self.assertEqual(state.snapshot()["barge_in_count"], 1)

    def test_barge_in_forces_server_auto_interrupt_off(self):
        config = replace(
            app.Config.from_env(),
            barge_in_enabled=True,
            vad_interrupt_response=True,
        )
        state = app.RuntimeState()
        client = app.RealtimeClient(config, FakeSpeaker(), state)
        ws = FakeWebSocket()
        client._ws = ws
        client._on_open(ws)
        turn_detection = json.loads(ws.sent[0])["session"]["audio"]["input"][
            "turn_detection"
        ]
        self.assertFalse(turn_detection["interrupt_response"])

    def test_interrupted_output_is_persisted_only_once_on_duplicate_events(self):
        config = replace(app.Config.from_env(), barge_in_enabled=True)
        state = app.RuntimeState()
        speaker = FakeSpeaker()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio_store = app.AudioStore(root / "replies", state)
            outputs = app.AssistantOutputStore(
                root / "assistant_outputs.jsonl", audio_store, state
            )
            client = app.RealtimeClient(config, speaker, state, outputs=outputs)
            ws = FakeWebSocket()
            client._ws = ws
            state.update(realtime_connected=True)
            client._normal_response_active = True
            client._active_response_id = "resp_once"
            client._active_output_item_id = "item_once"
            client._response_transcript = "已经说出的前半句"
            client._response_audio.extend(b"\0\0" * 9600)
            client.handle_barge_in(
                app.BargeInDecision(True, b"voice", 220, 0.1, 0.8)
            )
            duplicate = {
                "response_id": "resp_once",
                "item_id": "item_once",
                "transcript": "不应重复写入",
            }
            client._handle_assistant_transcript(duplicate)
            client._handle_assistant_transcript(duplicate)
            lines = (root / "assistant_outputs.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()
            self.assertEqual(len(lines), 1)
            self.assertTrue(json.loads(lines[0])["interrupted"])

    def test_barge_in_after_generation_done_updates_existing_record(self):
        config = replace(app.Config.from_env(), barge_in_enabled=True)
        state = app.RuntimeState()
        speaker = FakeSpeaker()
        speaker.active_id = "resp_already_done"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio_store = app.AudioStore(root / "replies", state)
            outputs = app.AssistantOutputStore(
                root / "assistant_outputs.jsonl", audio_store, state
            )
            outputs.append(
                "resp_already_done",
                "resp_already_done",
                "item_already_done",
                "完整生成但还没播完",
                {"generated_ms": 400, "played_ms": 0},
            )
            client = app.RealtimeClient(config, speaker, state, outputs=outputs)
            client._persisted_output_ids.add("resp_already_done")
            client._active_output_item_id = "item_already_done"
            ws = FakeWebSocket()
            client._ws = ws
            state.update(realtime_connected=True)

            client.handle_barge_in(
                app.BargeInDecision(True, b"voice", 220, 0.1, 0.8)
            )

            events = [json.loads(payload) for payload in ws.sent]
            self.assertEqual(
                [event["type"] for event in events],
                ["conversation.item.truncate", "input_audio_buffer.append"],
            )
            record = json.loads(
                (root / "assistant_outputs.jsonl").read_text(encoding="utf-8").strip()
            )
            self.assertTrue(record["interrupted"])
            self.assertEqual(record["played_ms"], 240)


    def test_health_fails_when_media_stops_refreshing(self):
        state = app.RuntimeState()
        state.update(
            realtime_connected=True,
            media_stale_after_seconds=20,
            media_startup_grace_seconds=0,
            last_frame_at=__import__("time").time(),
            last_audio_at=__import__("time").time(),
        )
        fresh = state.snapshot()
        self.assertTrue(fresh["ok"])
        self.assertTrue(fresh["video_streaming"])
        self.assertTrue(fresh["audio_streaming"])

        state.update(last_frame_at=__import__("time").time() - 30)
        stale = state.snapshot()
        self.assertFalse(stale["ok"])
        self.assertFalse(stale["media_ok"])
        self.assertFalse(stale["video_streaming"])

    def test_assistant_output_transcript_matches_persistent_wav_name(self):
        config = app.Config.from_env()
        state = app.RuntimeState()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio_store = app.AudioStore(root / "replies", state)
            outputs = app.AssistantOutputStore(
                root / "assistant_outputs.jsonl", audio_store, state
            )
            client = app.RealtimeClient(
                config, FakeSpeaker(), state, outputs=outputs
            )
            client._handle_assistant_transcript(
                {
                    "response_id": "resp_family_1",
                    "item_id": "item_reply_1",
                    "transcript": "从前有一只小猫。",
                }
            )
            text_path = root / "replies" / "reply-resp_family_1.txt"
            self.assertEqual(text_path.read_text(encoding="utf-8").strip(), "从前有一只小猫。")
            record = json.loads(
                (root / "assistant_outputs.jsonl").read_text(encoding="utf-8").strip()
            )
            self.assertEqual(record["response_id"], "resp_family_1")
            self.assertTrue(record["audio_file"].endswith("reply-resp_family_1.wav"))
            self.assertEqual(state.snapshot()["assistant_outputs_persisted"], 1)

    def test_interrupted_output_record_keeps_stream_timing_metadata(self):
        state = app.RuntimeState()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio_store = app.AudioStore(root / "replies", state)
            outputs = app.AssistantOutputStore(
                root / "assistant_outputs.jsonl", audio_store, state
            )
            outputs.append(
                "resp_cut",
                "resp_cut",
                "item_cut",
                "前半句话",
                {
                    "streamed": True,
                    "interrupted": True,
                    "generated_ms": 940,
                    "played_ms": 620,
                    "stream_id": "stream_cut",
                },
            )
            record = json.loads(
                (root / "assistant_outputs.jsonl").read_text(encoding="utf-8").strip()
            )
            self.assertTrue(record["interrupted"])
            self.assertEqual(record["generated_ms"], 940)
            self.assertEqual(record["played_ms"], 620)
            self.assertEqual(record["stream_id"], "stream_cut")

    def test_handoff_response_becomes_next_session_instructions(self):
        config = replace(app.Config.from_env(), vad_threshold=0.55)
        state = app.RuntimeState()
        client = app.RealtimeClient(config, FakeSpeaker(), state)
        client._handle_response_done(
            {
                "response": {
                    "metadata": {"purpose": client.HANDOFF_PURPOSE},
                    "output": [
                        {
                            "content": [
                                {"type": "output_text", "text": "用户正在找红色的球。"}
                            ]
                        }
                    ],
                }
            }
        )
        ws = FakeWebSocket()
        client._ws = ws
        client._on_open(ws)
        session_update = __import__("json").loads(ws.sent[0])
        self.assertIn("用户正在找红色的球", session_update["session"]["instructions"])
        self.assertEqual(
            session_update["session"]["audio"]["input"]["transcription"]["model"],
            "gpt-transcribe",
        )
        self.assertIsNone(
            session_update["session"]["audio"]["input"]["noise_reduction"]
        )
        self.assertEqual(
            session_update["session"]["audio"]["input"]["turn_detection"]["threshold"],
            0.55,
        )
        self.assertTrue(state.snapshot()["handoff_memory_present"])

    def test_unplanned_reconnect_replays_recent_completed_turns(self):
        config = app.Config.from_env()
        state = app.RuntimeState()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio_store = app.AudioStore(root / "replies", state)
            transcripts = app.TranscriptStore(root / "transcripts.jsonl", state)
            outputs = app.AssistantOutputStore(
                root / "assistant_outputs.jsonl", audio_store, state
            )
            transcripts.append("user_item", "灯现在亮着吗？")
            outputs.append("reply_item", "response_item", "assistant_item", "现在是亮着的。")
            client = app.RealtimeClient(
                config, FakeSpeaker(), state, transcripts, outputs
            )
            client._connected_since = 1
            client._session_had_dialogue = True
            client._response_audio.extend(b"partial")
            client._on_close(FakeWebSocket(), 1006, "network lost")

            ws = FakeWebSocket()
            client._ws = ws
            client._on_open(ws)
            instructions = json.loads(ws.sent[0])["session"]["instructions"]
            self.assertIn("用户: 灯现在亮着吗？", instructions)
            self.assertIn("助手: 现在是亮着的。", instructions)
            self.assertEqual(client._response_audio, b"")
            snapshot = state.snapshot()
            self.assertTrue(snapshot["reconnect_memory_present"])
            self.assertEqual(snapshot["unplanned_realtime_reconnects"], 1)

    def test_unplanned_reconnect_resends_recent_visual_context(self):
        config = app.Config.from_env()
        state = app.RuntimeState()
        client = app.RealtimeClient(config, FakeSpeaker(), state)
        first_ws = FakeWebSocket()
        client._ws = first_ws
        state.update(realtime_connected=True)
        client._connected_since = 1
        client.add_visual_context(b"recent-jpeg")
        client._on_close(first_ws, 1006, "network lost")

        second_ws = FakeWebSocket()
        client._ws = second_ws
        client._on_open(second_ws)
        client._on_message(
            second_ws,
            json.dumps(
                {
                    "type": "session.updated",
                    "session": {
                        "audio": {
                            "input": {
                                "transcription": {"model": "gpt-transcribe"}
                            }
                        }
                    },
                }
            ),
        )
        events = [json.loads(payload) for payload in second_ws.sent]
        image_events = [event for event in events if event["type"] == "conversation.item.create"]
        self.assertEqual(len(image_events), 1)
        self.assertIn(
            "cmVjZW50LWpwZWc=",
            image_events[0]["item"]["content"][0]["image_url"],
        )
        self.assertEqual(
            [part["type"] for part in image_events[0]["item"]["content"]],
            ["input_image"],
        )
        self.assertEqual(
            state.snapshot()["visual_context_resent_after_reconnect"], 1
        )

    def test_audio_input_tuning_is_sent_and_reported(self):
        config = replace(
            app.Config.from_env(),
            input_noise_reduction="far_field",
            vad_threshold=0.65,
            vad_prefix_padding_ms=350,
            vad_silence_duration_ms=700,
            vad_idle_timeout_ms=12000,
            vad_create_response=True,
            vad_interrupt_response=False,
        )
        state = app.RuntimeState()
        client = app.RealtimeClient(config, FakeSpeaker(), state)
        ws = FakeWebSocket()
        client._ws = ws
        client._on_open(ws)
        session_update = json.loads(ws.sent[0])
        input_audio = session_update["session"]["audio"]["input"]
        self.assertEqual(input_audio["noise_reduction"], {"type": "far_field"})
        self.assertEqual(
            input_audio["turn_detection"],
            {
                "type": "server_vad",
                "threshold": 0.65,
                "prefix_padding_ms": 350,
                "silence_duration_ms": 700,
                "idle_timeout_ms": 12000,
                "create_response": True,
                "interrupt_response": False,
            },
        )
        client._on_message(
            ws,
            json.dumps(
                {
                    "type": "session.updated",
                    "session": {"audio": {"input": input_audio}},
                }
            ),
        )
        snapshot = state.snapshot()
        self.assertEqual(
            snapshot["input_noise_reduction"], {"type": "far_field"}
        )
        self.assertEqual(snapshot["turn_detection"]["threshold"], 0.65)

    def test_advanced_realtime_session_tuning_is_sent(self):
        tool = {
            "type": "function",
            "name": "demo",
            "description": "test only",
            "parameters": {"type": "object", "properties": {}},
        }
        config = replace(
            app.Config.from_env(),
            voice="voice_custom123",
            output_speed=1.25,
            output_modality="audio",
            reasoning_effort="low",
            max_output_tokens=1024,
            include_transcription_logprobs=True,
            input_transcription_delay="high",
            input_transcription_keywords=["EBO", "药盒"],
            input_transcription_language="zh",
            input_transcription_languages=["zh", "en"],
            input_transcription_prompt="家庭机器人相关词汇",
            prompt_id="pmpt_123",
            prompt_version="7",
            prompt_variables={"city": "Toronto"},
            parallel_tool_calls=False,
            tools=[tool],
            tool_choice="none",
            tracing={"workflow_name": "ebo-home"},
            truncation_type="retention_ratio",
            truncation_retention_ratio=0.7,
            truncation_post_instructions=6000,
        )
        state = app.RuntimeState()
        client = app.RealtimeClient(config, FakeSpeaker(), state)
        ws = FakeWebSocket()
        client._ws = ws
        client._on_open(ws)

        session = json.loads(ws.sent[0])["session"]
        self.assertEqual(session["reasoning"], {"effort": "low"})
        self.assertEqual(session["max_output_tokens"], 1024)
        self.assertEqual(
            session["include"], ["item.input_audio_transcription.logprobs"]
        )
        transcription = session["audio"]["input"]["transcription"]
        self.assertEqual(transcription["delay"], "high")
        self.assertEqual(transcription["keywords"], ["EBO", "药盒"])
        self.assertEqual(transcription["language"], "zh")
        self.assertEqual(transcription["languages"], ["zh", "en"])
        self.assertEqual(transcription["prompt"], "家庭机器人相关词汇")
        self.assertEqual(session["audio"]["output"]["speed"], 1.25)
        self.assertEqual(
            session["audio"]["output"]["voice"], {"id": "voice_custom123"}
        )
        self.assertEqual(
            session["prompt"],
            {"id": "pmpt_123", "version": "7", "variables": {"city": "Toronto"}},
        )
        self.assertFalse(session["parallel_tool_calls"])
        self.assertEqual(session["tools"], [tool])
        self.assertEqual(session["tool_choice"], "none")
        self.assertEqual(session["tracing"], {"workflow_name": "ebo-home"})
        self.assertEqual(
            session["truncation"],
            {
                "type": "retention_ratio",
                "retention_ratio": 0.7,
                "token_limits": {"post_instructions": 6000},
            },
        )

    def test_realtime_truncation_can_be_auto_or_disabled(self):
        for truncation_type in ("auto", "disabled"):
            config = replace(
                app.Config.from_env(), truncation_type=truncation_type
            )
            client = app.RealtimeClient(config, FakeSpeaker(), app.RuntimeState())
            ws = FakeWebSocket()
            client._ws = ws
            client._on_open(ws)
            session = json.loads(ws.sent[0])["session"]
            self.assertEqual(session["truncation"], truncation_type)

    def test_semantic_vad_uses_eagerness_instead_of_server_thresholds(self):
        config = replace(
            app.Config.from_env(),
            turn_detection_type="semantic_vad",
            semantic_vad_eagerness="low",
        )
        state = app.RuntimeState()
        client = app.RealtimeClient(config, FakeSpeaker(), state)
        ws = FakeWebSocket()
        client._ws = ws
        client._on_open(ws)
        turn_detection = json.loads(ws.sent[0])["session"]["audio"]["input"][
            "turn_detection"
        ]
        self.assertEqual(turn_detection["type"], "semantic_vad")
        self.assertEqual(turn_detection["eagerness"], "low")
        self.assertNotIn("threshold", turn_detection)

    def test_input_transcript_is_persisted_and_reported_in_health(self):
        config = replace(app.Config.from_env(), vad_create_response=False)
        state = app.RuntimeState()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "transcripts.jsonl"
            transcripts = app.TranscriptStore(path, state)
            client = app.RealtimeClient(config, FakeSpeaker(), state, transcripts)
            ws = FakeWebSocket()
            client._ws = ws
            state.update(realtime_connected=True)
            client._on_message(
                ws,
                json.dumps(
                    {
                        "type": "session.updated",
                        "session": {
                            "audio": {
                                "input": {
                                    "transcription": {"model": "gpt-transcribe"}
                                }
                            }
                        },
                    }
                ),
            )
            client._on_message(
                ws,
                json.dumps(
                    {
                        "type": "conversation.item.input_audio_transcription.completed",
                        "item_id": "item_family_1",
                        "transcript": "你好，小猫。",
                        "languages": [{"code": "zh"}],
                    }
                ),
            )
            record = json.loads(path.read_text(encoding="utf-8").strip())
            self.assertEqual(record["item_id"], "item_family_1")
            self.assertEqual(record["transcript"], "你好，小猫。")
            snapshot = state.snapshot()
            self.assertTrue(snapshot["input_transcription_configured"])
            self.assertEqual(snapshot["user_transcripts_received"], 1)
            self.assertIsNotNone(snapshot["last_user_transcript_at"])
            self.assertEqual(snapshot["manual_responses_requested"], 1)
            self.assertEqual(json.loads(ws.sent[-1])["type"], "response.create")

    def test_empty_and_short_non_chinese_audio_do_not_create_responses(self):
        config = replace(app.Config.from_env(), vad_create_response=False)
        state = app.RuntimeState()
        client = app.RealtimeClient(config, FakeSpeaker(), state)
        ws = FakeWebSocket()
        client._ws = ws

        for item_id, transcript, language in (
            ("empty", "", []),
            ("noise_en", "The best.", [{"code": "en"}]),
            ("noise_es", "Sí, sí.", [{"code": "es"}]),
        ):
            client._handle_input_transcript(
                {
                    "item_id": item_id,
                    "transcript": transcript,
                    "languages": language,
                }
            )

        self.assertEqual(ws.sent, [])
        self.assertEqual(state.snapshot()["input_turns_ignored"], 3)

    def test_valid_transcript_requests_only_one_manual_response(self):
        config = replace(app.Config.from_env(), vad_create_response=False)
        state = app.RuntimeState()
        client = app.RealtimeClient(config, FakeSpeaker(), state)
        ws = FakeWebSocket()
        client._ws = ws
        state.update(realtime_connected=True)
        event = {
            "item_id": "valid_turn",
            "transcript": "你能看到我吗？",
            "languages": [{"code": "zh"}],
        }

        client._handle_input_transcript(event)
        client._handle_input_transcript(event)

        self.assertEqual(len(ws.sent), 1)
        self.assertEqual(json.loads(ws.sent[0])["type"], "response.create")
        self.assertEqual(state.snapshot()["manual_responses_requested"], 1)

    def test_null_response_metadata_does_not_break_callback(self):
        config = app.Config.from_env()
        state = app.RuntimeState()
        client = app.RealtimeClient(config, FakeSpeaker(), state)
        ws = FakeWebSocket()
        client._on_message(
            ws,
            json.dumps({"type": "response.created", "response": {"metadata": None}}),
        )
        client._on_message(
            ws,
            json.dumps({"type": "response.done", "response": {"metadata": None}}),
        )
        self.assertTrue(client._session_had_dialogue)

    def test_planned_rotation_closes_socket_and_updates_health(self):
        config = app.Config.from_env()
        state = app.RuntimeState()
        client = app.RealtimeClient(config, FakeSpeaker(), state)
        ws = FakeWebSocket()
        client._ws = ws
        state.update(realtime_connected=True)
        client._rotate_session()
        snapshot = state.snapshot()
        self.assertTrue(ws.closed)
        self.assertEqual(snapshot["realtime_session_rollovers"], 1)
        self.assertIsNotNone(snapshot["last_session_rollover_at"])

    def test_empty_session_rotates_without_paid_handoff_summary(self):
        config = replace(
            app.Config.from_env(),
            session_refresh_seconds=60,
            session_hard_deadline_seconds=120,
        )
        state = app.RuntimeState()
        client = app.RealtimeClient(config, FakeSpeaker(), state)
        ws = FakeWebSocket()
        client._ws = ws
        client._session_started_monotonic = 1
        state.update(realtime_connected=True)
        client._rotation_step(70)
        self.assertTrue(ws.closed)
        self.assertEqual(ws.sent, [])

    def test_active_conversation_prepares_handoff_before_rotation(self):
        config = replace(
            app.Config.from_env(),
            session_refresh_seconds=60,
            session_hard_deadline_seconds=120,
        )
        state = app.RuntimeState()
        client = app.RealtimeClient(config, FakeSpeaker(), state)
        ws = FakeWebSocket()
        client._ws = ws
        client._session_started_monotonic = 1
        client._session_had_dialogue = True
        state.update(realtime_connected=True)
        client._rotation_step(70)
        event = __import__("json").loads(ws.sent[0])
        self.assertEqual(event["response"]["conversation"], "none")
        self.assertEqual(
            event["response"]["metadata"]["purpose"], client.HANDOFF_PURPOSE
        )
        self.assertFalse(ws.closed)

    def test_visual_item_id_is_valid_and_previous_item_waits_for_ack(self):
        config = app.Config.from_env()
        state = app.RuntimeState()
        client = app.RealtimeClient(config, FakeSpeaker(), state)
        ws = FakeWebSocket()
        client._ws = ws
        state.update(realtime_connected=True)

        client.add_visual_context(b"jpeg-one")
        first_create = json.loads(ws.sent[-1])
        first_id = first_create["item"]["id"]
        self.assertEqual(
            [part["type"] for part in first_create["item"]["content"]],
            ["input_image"],
        )
        self.assertLessEqual(len(first_id), 32)
        self.assertEqual(first_create["event_id"], f"create_{first_id}")
        self.assertIsNone(client._latest_image_id)
        self.assertEqual(client._pending_image_id, first_id)
        self.assertFalse(
            any(json.loads(payload)["type"] == "conversation.item.delete" for payload in ws.sent)
        )

        client._on_message(
            ws,
            json.dumps({"type": "conversation.item.added", "item": {"id": first_id}}),
        )
        self.assertEqual(client._latest_image_id, first_id)
        self.assertIsNone(client._pending_image_id)
        self.assertEqual(state.snapshot()["visual_context_items_added"], 1)

        client.add_visual_context(b"jpeg-two")
        second_create = json.loads(ws.sent[-1])
        second_id = second_create["item"]["id"]
        self.assertFalse(
            any(
                json.loads(payload).get("item_id") == first_id
                for payload in ws.sent
                if json.loads(payload)["type"] == "conversation.item.delete"
            )
        )
        client._on_message(
            ws,
            json.dumps({"type": "conversation.item.added", "item": {"id": second_id}}),
        )
        delete = json.loads(ws.sent[-1])
        self.assertEqual(delete["type"], "conversation.item.delete")
        self.assertEqual(delete["item_id"], first_id)
        self.assertEqual(client._latest_image_id, second_id)

    def test_visual_create_error_clears_pending_item(self):
        config = app.Config.from_env()
        state = app.RuntimeState()
        client = app.RealtimeClient(config, FakeSpeaker(), state)
        ws = FakeWebSocket()
        client._ws = ws
        state.update(realtime_connected=True)
        client.add_visual_context(b"jpeg")
        create = json.loads(ws.sent[-1])
        client._on_message(
            ws,
            json.dumps(
                {
                    "type": "error",
                    "error": {
                        "code": "invalid_value",
                        "message": "rejected",
                        "event_id": create["event_id"],
                    },
                }
            ),
        )
        self.assertIsNone(client._pending_image_id)
        self.assertIsNone(client._latest_image_id)


if __name__ == "__main__":
    unittest.main()
