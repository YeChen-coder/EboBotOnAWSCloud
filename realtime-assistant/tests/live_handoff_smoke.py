"""Minimal live smoke test for the Realtime out-of-band handoff response."""

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
    ws = websocket.create_connection(
        f"wss://api.openai.com/v1/realtime?model={model}",
        header=[f"Authorization: Bearer {api_key}"],
        timeout=20,
    )
    try:
        deadline = time.monotonic() + 20
        if not any(event.get("type") == "session.created" for event in receive(ws, deadline)):
            raise RuntimeError("session.created not received")
        ws.send(
            json.dumps(
                {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "message",
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": "这是连接轮换测试：用户偏好使用中文。",
                            }
                        ],
                    },
                }
            )
        )
        ws.send(
            json.dumps(
                {
                    "type": "response.create",
                    "response": {
                        "conversation": "none",
                        "metadata": {"purpose": "ebo_session_handoff"},
                        "output_modalities": ["text"],
                        "max_output_tokens": 600,
                        "instructions": "生成一句简短内部记忆，不要解释。",
                    },
                }
            )
        )
        for event in receive(ws, time.monotonic() + 30):
            response = event.get("response", {})
            if (
                event.get("type") == "response.done"
                and response.get("metadata", {}).get("purpose")
                == "ebo_session_handoff"
            ):
                text = "".join(
                    part.get("text", "") or part.get("transcript", "")
                    for item in response.get("output", [])
                    for part in item.get("content", [])
                )
                print(
                    json.dumps(
                        {
                            "ok": response.get("status") == "completed",
                            "status": response.get("status"),
                            "summary_chars": len(text),
                            "output_types": [
                                item.get("type") for item in response.get("output", [])
                            ],
                            "content_types": [
                                part.get("type")
                                for item in response.get("output", [])
                                for part in item.get("content", [])
                            ],
                        }
                    )
                )
                return
        raise RuntimeError("handoff response.done not received")
    finally:
        ws.close()


if __name__ == "__main__":
    main()
