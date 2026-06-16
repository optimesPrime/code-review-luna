from __future__ import annotations
import json
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class LunaWebServer:
    """Temporary local HTTP server for the Luna web report.

    Serves the generated HTML report, accepts POST /run to execute luna
    commands, streams output via SSE on GET /stream, and shuts down
    automatically 10 s after the last heartbeat (browser tab close).
    """

    HEARTBEAT_TIMEOUT = 10

    def __init__(self, html_path: str, port: int = 0):
        self.html_path = html_path
        self._port = port
        self._server: HTTPServer | None = None
        self._last_heartbeat: float = time.time()
        self._output_lines: list[str] = []
        self._run_done: bool = True
        self._output_lock = threading.Lock()
        self._stopped = False

    def start(self) -> int:
        """Start server in background thread. Returns the actual port."""
        handler = self._make_handler()
        self._server = ThreadingHTTPServer(("127.0.0.1", self._port), handler)
        self._port = self._server.server_address[1]

        t = threading.Thread(target=self._server.serve_forever, daemon=False)
        t.start()

        watcher = threading.Thread(target=self._heartbeat_watcher, daemon=True)
        watcher.start()

        return self._port

    def stop(self) -> None:
        self._stopped = True
        if self._server:
            self._server.shutdown()

    def _heartbeat_watcher(self) -> None:
        while not self._stopped:
            time.sleep(2)
            if time.time() - self._last_heartbeat > self.HEARTBEAT_TIMEOUT:
                self.stop()
                return

    def _make_handler(self):
        server_ref = self

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass  # 静默日志

            def _cors(self):
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")

            def do_OPTIONS(self):
                self.send_response(204)
                self._cors()
                self.end_headers()

            def do_GET(self):
                if self.path in ("/", "/index.html"):
                    self._serve_file()
                elif self.path == "/stream":
                    self._serve_stream()
                else:
                    self.send_response(404)
                    self.end_headers()

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length) if length else b"{}"
                if self.path == "/heartbeat":
                    server_ref._last_heartbeat = time.time()
                    self.send_response(200)
                    self._cors()
                    self.end_headers()
                elif self.path == "/run":
                    self._handle_run(body)
                else:
                    self.send_response(404)
                    self.end_headers()

            def _serve_file(self):
                try:
                    content = Path(server_ref.html_path).read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self._cors()
                    self.end_headers()
                    self.wfile.write(content)
                except FileNotFoundError:
                    self.send_response(404)
                    self.end_headers()

            def _handle_run(self, body: bytes):
                try:
                    cmd = json.loads(body).get("cmd", "")
                except json.JSONDecodeError:
                    cmd = ""
                if not cmd:
                    self.send_response(400)
                    self.end_headers()
                    return

                with server_ref._output_lock:
                    server_ref._output_lines = []
                    server_ref._run_done = False

                self.send_response(200)
                self._cors()
                self.end_headers()

                def _run():
                    try:
                        proc = subprocess.Popen(
                            cmd, shell=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True,
                        )
                        for line in proc.stdout:
                            with server_ref._output_lock:
                                server_ref._output_lines.append(line.rstrip())
                        proc.wait()
                    except Exception as e:
                        with server_ref._output_lock:
                            server_ref._output_lines.append(f"Error: {e}")
                    finally:
                        with server_ref._output_lock:
                            server_ref._run_done = True

                threading.Thread(target=_run, daemon=True).start()

            def _serve_stream(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self._cors()
                self.end_headers()
                sent = 0
                deadline = time.time() + 60
                while time.time() < deadline:
                    with server_ref._output_lock:
                        lines = server_ref._output_lines[sent:]
                        done = server_ref._run_done
                    for line in lines:
                        data = f"data: {json.dumps(line)}\n\n"
                        try:
                            self.wfile.write(data.encode())
                            self.wfile.flush()
                        except BrokenPipeError:
                            return
                        sent += 1
                    if done and not lines:
                        # 命令已结束且无新输出，发送结束事件
                        try:
                            self.wfile.write(b"event: done\ndata: {}\n\n")
                            self.wfile.flush()
                        except BrokenPipeError:
                            pass
                        return
                    if not lines:
                        time.sleep(0.1)

        return _Handler

    @property
    def port(self) -> int:
        return self._port
