import sys, os, time, json, urllib.request, threading, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from web_server import LunaWebServer


def _make_server(content="<html>test</html>"):
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".html", mode="w")
    tmp.write(content)
    tmp.close()
    s = LunaWebServer(html_path=tmp.name, port=0)
    port = s.start()
    return s, port, tmp.name


def test_serve_html():
    s, port, path = _make_server("<html>hello luna</html>")
    try:
        resp = urllib.request.urlopen(f"http://localhost:{port}/")
        assert b"hello luna" in resp.read()
    finally:
        s.stop()
        os.unlink(path)


def test_heartbeat_resets_timer():
    s, port, path = _make_server()
    try:
        s._last_heartbeat = time.time() - 8
        urllib.request.urlopen(
            urllib.request.Request(f"http://localhost:{port}/heartbeat",
                                   data=b"{}", method="POST")
        )
        assert time.time() - s._last_heartbeat < 2
    finally:
        s.stop()
        os.unlink(path)


def test_run_echo_command():
    s, port, path = _make_server()
    try:
        body = json.dumps({"cmd": "echo hello_luna"}).encode()
        req = urllib.request.Request(
            f"http://localhost:{port}/run",
            data=body, method="POST",
            headers={"Content-Type": "application/json"},
        )
        resp = urllib.request.urlopen(req)
        assert resp.status == 200
    finally:
        s.stop()
        os.unlink(path)
