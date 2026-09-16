"""Stale browser / HA commands must be blocked before they reach MQTT."""
import io
import json
from unittest.mock import Mock

import pytest
import panel


@pytest.mark.parametrize("payload", ["on", "off", "false", "0"])
def test_old_http_listen_is_rejected_not_forwarded(monkeypatch, payload):
    mqtt = Mock()
    monkeypatch.setattr(panel, "_client", mqtt)
    handler = panel.Handler.__new__(panel.Handler)
    body = json.dumps({"node": "ebo", "suffix": "listen/set", "payload": payload}).encode()
    handler.path = "/api/cmd"
    handler.headers = {"Content-Length": str(len(body))}
    handler.rfile = io.BytesIO(body)
    handler._authed = lambda: True
    handler._send = Mock()
    handler.do_POST()
    assert handler._send.call_args.args[0] == 409
    assert json.loads(handler._send.call_args.args[1])["error"] == "legacy_listen_control"
    mqtt.publish.assert_not_called()


@pytest.mark.parametrize("payload", ["on", "off"])
def test_explicit_global_microphone_command_is_forwarded(monkeypatch, payload):
    mqtt = Mock()
    monkeypatch.setattr(panel, "_client", mqtt)
    handler = panel.Handler.__new__(panel.Handler)
    body = json.dumps({"node": "ebo", "suffix": "microphone/set", "payload": payload}).encode()
    handler.path = "/api/cmd"
    handler.headers = {"Content-Length": str(len(body))}
    handler.rfile = io.BytesIO(body)
    handler._authed = lambda: True
    handler._send = Mock()
    handler.do_POST()
    assert handler._send.call_args.args[0] == 200
    mqtt.publish.assert_called_once_with("ebo/microphone/set", payload)


def test_microphone_command_still_requires_authentication(monkeypatch):
    mqtt = Mock()
    monkeypatch.setattr(panel, "_client", mqtt)
    handler = panel.Handler.__new__(panel.Handler)
    handler._authed = lambda: False
    handler._send = Mock()
    handler.do_POST()
    assert handler._send.call_args.args[0] == 403
    mqtt.publish.assert_not_called()


def test_html_and_json_not_cached():
    for content_type in ("text/html; charset=utf-8", "application/json"):
        handler = panel.Handler.__new__(panel.Handler)
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.wfile = io.BytesIO()
        handler._send(200, "test", content_type)
        handler.send_header.assert_any_call("Cache-Control", "no-store, max-age=0")
