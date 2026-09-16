"""Verify that a real Realtime session accepts input transcription config."""

import json
import os
import time

import websocket


def receive(ws, deadline):
    while time.monotonic() < deadline:
        event = json.loads(ws.recv())
        if event.get("type") == "error":
            raise RuntimeError(str(event.get("error", event)))
        yield event


def main():
    api_key = os.environ["OPENAI_API_KEY"]
    model = os.getenv("OPENAI_REALTIME_MODEL", "gpt-realtime-2.1")
    transcription_model = os.getenv(
        "OPENAI_INPUT_TRANSCRIPTION_MODEL", "gpt-transcribe"
    )
    ws = websocket.create_connection(
        f"wss://api.openai.com/v1/realtime?model={model}",
        header=[f"Authorization: Bearer {api_key}"],
        timeout=20,
    )
    try:
        if not any(
            event.get("type") == "session.created"
            for event in receive(ws, time.monotonic() + 20)
        ):
            raise RuntimeError("session.created not received")
        ws.send(
            json.dumps(
                {
                    "type": "session.update",
                    "session": {
                        "type": "realtime",
                        "model": model,
                        "output_modalities": ["audio"],
                        "audio": {
                            "input": {
                                "format": {"type": "audio/pcm", "rate": 24000},
                                "transcription": {"model": transcription_model},
                                "turn_detection": {
                                    "type": "server_vad",
                                    "create_response": True,
                                    "interrupt_response": True,
                                },
                            },
                            "output": {
                                "format": {"type": "audio/pcm", "rate": 24000},
                                "voice": "marin",
                            },
                        },
                    },
                }
            )
        )
        for event in receive(ws, time.monotonic() + 20):
            if event.get("type") != "session.updated":
                continue
            session = event.get("session", {})
            configured = (
                session.get("audio", {})
                .get("input", {})
                .get("transcription", {})
                .get("model")
            )
            print(
                json.dumps(
                    {
                        "ok": configured == transcription_model,
                        "transcription_model": configured,
                    }
                )
            )
            return
        raise RuntimeError("session.updated not received")
    finally:
        ws.close()


if __name__ == "__main__":
    main()
