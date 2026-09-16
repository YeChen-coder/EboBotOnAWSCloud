import time
import unittest
from unittest import mock

from test_app import app


class SourceAudioHealthTests(unittest.TestCase):
    def setUp(self):
        self.state = app.RuntimeState()
        now = time.time()
        self.health = {"observed_at": now, "status": "receiving", "source_audio_ok": True,
                       "last_packet_at": now, "last_pcm_at": now, "listen_enabled": True}
        self.state.update(realtime_connected=True, engine_audio_monitor_enabled=True,
                          engine_audio_checked_at=now, engine_audio_health=self.health)
        self.state.mark_media("frame")
        self.state.mark_media("audio")

    def test_receiving_source_and_transport_are_healthy(self):
        self.assertTrue(self.state.snapshot()["ok"])

    def test_rtsp_padding_cannot_hide_muted_or_missing_source(self):
        for status in ("muted", "no_source_packets", "no_decoded_pcm", "disconnected"):
            with self.subTest(status=status):
                self.state.update(engine_audio_health=dict(self.health, status=status, source_audio_ok=False))
                snapshot = self.state.snapshot()
                self.assertTrue(snapshot["audio_streaming"])
                self.assertFalse(snapshot["source_audio_ok"])
                self.assertFalse(snapshot["ok"])

    def test_old_retained_mqtt_health_or_packet_evidence_cannot_stay_green(self):
        self.state.update(engine_audio_health=dict(self.health, observed_at=time.time()-30))
        self.assertEqual(self.state.snapshot()["source_audio_status"], "monitor_stale")
        self.state.update(engine_audio_health=dict(self.health, last_packet_at=time.time()-30))
        self.assertEqual(self.state.snapshot()["source_audio_status"], "source_stale")
        self.state.update(engine_audio_health=self.health, engine_audio_checked_at=time.time()-30)
        self.assertFalse(self.state.snapshot()["ok"])

    def test_monitor_http_failure_is_visible(self):
        self.state.update(engine_audio_error="TimeoutError")
        self.assertEqual(self.state.snapshot()["source_audio_status"], "monitor_error")
        self.assertFalse(self.state.snapshot()["ok"])

    def test_monitor_never_issues_wake_or_unmute_commands(self):
        api = mock.Mock()
        api.audio_health.return_value = dict(self.health, status="muted", source_audio_ok=False)
        capture = app.MediaCapture(app.Config.from_env(), None, None, None, api, self.state)
        with mock.patch.object(capture._stop, "wait", side_effect=lambda _seconds: capture._stop.set()):
            capture.audio_health_loop()
        api.command.assert_not_called()
        api.wake_if_due.assert_not_called()
        self.assertEqual(self.state.snapshot()["source_audio_status"], "muted")


if __name__ == "__main__":
    unittest.main()
