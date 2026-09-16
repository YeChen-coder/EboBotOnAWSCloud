"""Live smoke test for Realtime image item creation and deletion."""

import base64
import json
import os
import time
import uuid

import cv2
import numpy as np
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
        if not any(
            event.get("type") == "session.created"
            for event in receive(ws, time.monotonic() + 20)
        ):
            raise RuntimeError("session.created not received")
        image = np.zeros((64, 64, 3), dtype=np.uint8)
        image[:, :, 1] = 128
        ok, encoded = cv2.imencode(".jpg", image)
        if not ok:
            raise RuntimeError("JPEG encoding failed")
        item_id = "img_" + uuid.uuid4().hex[:28]
        ws.send(
            json.dumps(
                {
                    "event_id": f"create_{item_id}",
                    "type": "conversation.item.create",
                    "item": {
                        "id": item_id,
                        "type": "message",
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": "协议测试图片，不需要回答。"},
                            {
                                "type": "input_image",
                                "image_url": "data:image/jpeg;base64,"
                                + base64.b64encode(encoded.tobytes()).decode("ascii"),
                            },
                        ],
                    },
                }
            )
        )
        for event in receive(ws, time.monotonic() + 20):
            if (
                event.get("type") == "conversation.item.added"
                and event.get("item", {}).get("id") == item_id
            ):
                ws.send(
                    json.dumps(
                        {
                            "event_id": f"delete_{item_id}",
                            "type": "conversation.item.delete",
                            "item_id": item_id,
                        }
                    )
                )
                break
        else:
            raise RuntimeError("conversation.item.added not received")
        for event in receive(ws, time.monotonic() + 20):
            if (
                event.get("type") == "conversation.item.deleted"
                and event.get("item_id") == item_id
            ):
                print(json.dumps({"ok": True, "item_id_length": len(item_id)}))
                return
        raise RuntimeError("conversation.item.deleted not received")
    finally:
        ws.close()


if __name__ == "__main__":
    main()
