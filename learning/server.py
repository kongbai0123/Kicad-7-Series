"""Local-only learning dashboard. Run with python learning/server.py."""
from __future__ import annotations

import argparse
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import html
import json
import mimetypes
import os
from pathlib import Path
import sys
from urllib.parse import unquote, urlparse
import webbrowser

import checks
import state
from reader import render_document

WEB = state.LEARNING / "web"
MAX_BODY = 32768


def run_check(payload):
    lesson = state.lesson_by_id(payload.get("lessonId"))
    step = next((item for item in lesson["steps"] if item["id"] == payload.get("stepId")), None)
    if step is None:
        raise state.StateError("找不到這個操作步驟。")
    return checks.run_step(lesson, step, state.progress())


class Handler(BaseHTTPRequestHandler):
    server_version = "KiCadLearning/1"

    def log_message(self, fmt, *args):
        print(f"{self.log_date_time_string()} {fmt % args}", flush=True)

    def _check_host(self):
        host = self.headers.get("Host", "")
        valid = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        if host not in valid:
            raise state.StateError("請由本機學習入口開啟。", 403)
        return host

    def send_bytes(self, data, content_type, status=200, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'")
        if extra:
            for key, value in extra.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, value, status=200, extra=None):
        self.send_bytes(json.dumps(value, ensure_ascii=False).encode("utf-8"),
                        "application/json; charset=utf-8", status, extra)

    def file(self, path, attachment=False):
        if not path.is_file():
            raise state.StateError("找不到檔案。", 404)
        mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        if mime.startswith("text/") or path.suffix == ".js":
            mime += "; charset=utf-8"
        extra = {"Content-Disposition": "attachment"} if attachment else None
        self.send_bytes(path.read_bytes(), mime, extra=extra)

    def do_GET(self):
        try:
            self._check_host()
            route = unquote(urlparse(self.path).path)
            if route == "/api/health":
                return self.send_json({"application": "kicad-guided-learning", "project": str(state.REPO), "ready": True})
            if route == "/api/bootstrap":
                return self.send_json({
                    "projectRoot": str(state.REPO),
                    "catalog": state.catalog(), "progress": state.progress(),
                    "journal": state.read_json(state.LEARNING / "resources.json")["journal"],
                    "guides": state.read_json(state.LEARNING / "resources.json")["guides"]})
            if route == "/api/export":
                return self.send_json(state.progress(), extra={"Content-Disposition": 'attachment; filename="KiCad-learning-progress.json"'})
            if route in {"/", "/index.html", "/app.css", "/app.js"}:
                name = "index.html" if route in {"/", "/index.html"} else route.lstrip("/")
                return self.file(WEB / name)
            if route.startswith("/files/"):
                path = state.safe_path(route[len("/files/"):])
                if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".pdf", ".zip", ".gbr", ".drl", ".gbrjob", ".json", ".txt", ".md", ".kicad_pro", ".kicad_sch", ".kicad_pcb", ".kicad_wks", ".kicad_sym", ".kicad_mod", ".step", ".stp"}:
                    raise state.StateError("這個檔案請從專案資料夾開啟。", 403)
                return self.file(path, attachment=path.suffix.lower() == ".zip")
            if route.startswith("/read/"):
                path = state.safe_path(route[len("/read/"):])
                if path.suffix != ".md" or not path.is_file():
                    raise state.StateError("找不到學習紀錄。", 404)
                page = render_document(path, state.REPO)
                return self.send_bytes(page.encode("utf-8"), "text/html; charset=utf-8")
            if route == "/favicon.ico":
                return self.send_bytes(b"", "image/x-icon", 204)
            raise state.StateError("找不到這個頁面。", 404)
        except state.StateError as exc:
            self.send_json({"error": str(exc)}, exc.status)
        except (OSError, ValueError, KeyError) as exc:
            self.send_json({"error": f"讀取學習資料失敗：{exc}"}, 500)

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BODY:
                raise state.StateError("操作內容大小不正確。", 413)
            body = self.rfile.read(length)
            host = self._check_host()
            origin = self.headers.get("Origin")
            if origin and origin != f"http://{host}":
                raise state.StateError("請由同一個學習入口操作。", 403)
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise state.StateError("請以學習入口傳送操作。", 415)
            payload = json.loads(body)
            if not isinstance(payload, dict):
                raise state.StateError("操作內容格式不正確。")
            route = urlparse(self.path).path
            if route == "/api/progress":
                verification = None
                if payload.get("action") == "confirm_step":
                    verification = run_check(payload)
                    if verification and not verification["passed"]:
                        return self.send_json({"error": "自動檢查還沒通過，請依下方說明修正後再確認。",
                                               "check": verification}, 409)
                return self.send_json({"progress": state.mutate(payload, verification=verification)})
            if route == "/api/check":
                return self.send_json({"check": run_check(payload)})
            if route == "/api/workspace":
                return self.send_json(state.workspace(payload))
            if route == "/api/open":
                path = state.open_target(payload.get("lessonId"), payload.get("target"))
                if os.name != "nt":
                    raise state.StateError("此啟動方式供 Windows 使用，請由資料夾開啟專案。")
                os.startfile(str(path))
                return self.send_json({"ok": True, "path": str(path)})
            raise state.StateError("找不到這個操作。", 404)
        except state.StateError as exc:
            self.send_json({"error": str(exc)}, exc.status)
        except (OSError, ValueError, KeyError) as exc:
            self.send_json({"error": f"儲存或開啟失敗：{exc}"}, 400)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    state.catalog()
    state.progress()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"KiCad learning: http://127.0.0.1:{args.port}/", flush=True)
    if args.open:
        webbrowser.open(f"http://127.0.0.1:{args.port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
