"""A small in-memory web chat. Run with: python3 chat.py"""

import argparse
import json
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


MESSAGES = []
LOCK = threading.Lock()
NEXT_ID = 1
PAGE = Path(__file__).with_name("index.html")


class ChatHandler(BaseHTTPRequestHandler):
    def respond(self, status, body, content_type="application/json; charset=utf-8"):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/":
            self.respond(200, PAGE.read_text(encoding="utf-8"), "text/html; charset=utf-8")
        elif self.path == "/api/messages":
            with LOCK:
                data = json.dumps(MESSAGES, ensure_ascii=False)
            self.respond(200, data)
        else:
            self.respond(404, '{"error":"页面不存在"}')

    def do_POST(self):
        global NEXT_ID
        if self.path != "/api/messages":
            self.respond(404, '{"error":"页面不存在"}')
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 8192:
                raise ValueError("请求大小无效")
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError("请求必须是对象")
            name, text = data.get("name"), data.get("text")
            if not isinstance(name, str) or not isinstance(text, str):
                raise ValueError("昵称和消息必须是文字")
            name, text = name.strip(), text.strip()
            if not 1 <= len(name) <= 20 or not 1 <= len(text) <= 500:
                raise ValueError("昵称需为 1–20 字，消息需为 1–500 字")
        except (ValueError, UnicodeDecodeError):
            self.respond(400, json.dumps({"error": "请输入有效昵称和消息（昵称最多20字，消息最多500字）"}, ensure_ascii=False))
            return
        with LOCK:
            message = {"id": NEXT_ID, "name": name, "text": text,
                       "time": datetime.now().astimezone().isoformat(timespec="seconds")}
            NEXT_ID += 1
            MESSAGES.append(message)
            del MESSAGES[:-100]
        self.respond(201, json.dumps(message, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="简单网页聊天室")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), ChatHandler)
    print(f"聊天室已启动，监听 {args.host}:{args.port}，按 Ctrl+C 停止。", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
