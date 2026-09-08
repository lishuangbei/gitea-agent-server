#!/usr/bin/env python3
"""Test the built Git gateway image against an isolated mock Gitea backend.

Run on the Docker host: python3 tests/test-git-gateway.py
This does not build the image or use an existing container, network or volume.
"""

import base64
import http.server
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid


IMAGE = "gitea-agent-server:local"
CONFIG = "/etc/nginx/nginx.conf"
UTILITY = "/usr/local/libexec/configure-git-gateway.py"
AUTH_DIRECTORY = Path("/data/git-gateway")
TEST_PASSWORD = "fake-test-only-password"
TEST_AUTHORIZATION = "Basic " + base64.b64encode(
    f"gitadmin:{TEST_PASSWORD}".encode()
).decode()


def check(condition, message):
    if not condition:
        raise AssertionError(message)


class Backend(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.handle_request()

    def do_POST(self):
        self.handle_request()

    def handle_request(self):
        if self.path == "/api/v1/user":
            valid = self.headers.get("Authorization") == TEST_AUTHORIZATION
            data = {"login": "gitadmin"} if valid else {"error": "unauthorized"}
            status = 200 if valid else 401
        else:
            count = 0
            if self.headers.get("Transfer-Encoding") == "chunked":
                while True:
                    size = int(self.rfile.readline().strip(), 16)
                    if not size:
                        self.rfile.readline()
                        break
                    count += len(self.rfile.read(size))
                    self.rfile.read(2)
            else:
                count = len(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            data = {
                "authorization": self.headers.get("Authorization"),
                "cookie": self.headers.get("Cookie"),
                "host": self.headers.get("Host"),
                "proto": self.headers.get("X-Forwarded-Proto"),
                "user": self.headers.get("X-Webauth-User"),
                "encoding": self.headers.get("Accept-Encoding"),
                "count": count,
            }
            if self.path.endswith("/objects/batch"):
                data = {
                    "objects": [{"actions": {"download": {"header": {
                        "Authorization": self.headers.get("Authorization"),
                    }}}}],
                }
            status = 200
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header(
            "Content-Type",
            "application/vnd.git-lfs+json"
            if self.path.endswith("/objects/batch") else "application/json",
        )
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def inside_container():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 3001), Backend)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    os.makedirs("/run/nginx", exist_ok=True)
    AUTH_DIRECTORY.mkdir(parents=True, exist_ok=True)
    subprocess.run(["nginx", "-t", "-c", CONFIG], check=True, capture_output=True)
    nginx = subprocess.Popen(
        ["nginx", "-c", CONFIG, "-g", "daemon off;"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    headers = {
        "Host": "test-name.tail.ts.net",
        "X-Forwarded-Proto": "https",
        "Authorization": "Basic client-only",
        "Cookie": "session=client",
        "X-Webauth-User": "gitadmin",
    }
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def query(path, data=None):
        request = urllib.request.Request(
            "http://127.0.0.1:3000" + path, headers=headers, data=data,
        )
        with opener.open(request, timeout=10) as response:
            return json.load(response)

    def wait_until(predicate):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                if predicate():
                    return
            except urllib.error.URLError:
                pass
            time.sleep(0.02)
        raise AssertionError("Gateway did not become ready within five seconds.")

    def configure(password):
        return subprocess.run(
            ["python3", UTILITY],
            input=f"gitadmin\0{password}\0".encode(),
            capture_output=True,
            timeout=35,
        )

    try:
        wait_until(lambda: query("/")["authorization"] == "Basic client-only")
        check(
            query("/gitadmin/repo.git/info/refs")["authorization"] == "Basic client-only",
            "Bootstrap without an auth file must pass through client credentials.",
        )
        check(configure(TEST_PASSWORD).returncode == 0, "Credential configuration failed.")
        auth_file = AUTH_DIRECTORY / "auth.conf"
        check(auth_file.stat().st_mode & 0o777 == 0o600, "Auth file must be private.")
        check(auth_file.stat().st_uid == 0, "Auth file must belong to root.")
        subprocess.run(
            ["nginx", "-s", "reload", "-c", CONFIG], check=True, capture_output=True,
        )
        wait_until(
            lambda: query("/gitadmin/repo.git/info/refs")["authorization"]
            == TEST_AUTHORIZATION,
        )
        git_routes = [
            "/gitadmin/repo.git/info/refs",
            "/gitadmin/repo/info/refs",
            "/gitadmin/repo.git/git-upload-pack",
            "/gitadmin/repo.git/git-receive-pack",
            "/gitadmin/repo.git/info/lfs/verify",
            "/gitadmin/repo.git/info/lfs/locks",
            "/gitadmin/repo.git/info/lfs/locks/42/unlock",
            "/gitadmin/repo.git/info/lfs/objects/" + "a" * 64 + "/2",
        ]
        for path in git_routes:
            result = query(path)
            check(result["authorization"] == TEST_AUTHORIZATION, f"Missing Git auth: {path}")
            check(result["cookie"] is None, f"Client cookie reached a Git route: {path}")
            check(result["user"] is None, f"Identity header reached the backend: {path}")
            check(result["host"] == "test-name.tail.ts.net", "Host was not preserved.")
            check(result["proto"] == "https", "HTTPS forwarding scheme was not preserved.")

        ordinary_routes = [
            "/", "/user/settings", "/gitadmin/repo", "/gitadmin/repo/settings",
            "/api/v1/info/refs", "/gitadmin/repo.git/info/lfs/objects/not-an-oid",
        ]
        for path in ordinary_routes:
            result = query(path)
            check(result["authorization"] == "Basic client-only", f"Web/API auth changed: {path}")
            check(result["cookie"] == "session=client", f"Web/API cookie changed: {path}")
            check(result["user"] is None, f"Identity header reached the backend: {path}")

        result = query("/gitadmin/repo.git/info/lfs/objects/batch", b"{}")
        check(
            result["objects"][0]["actions"]["download"]["header"]["Authorization"] == "",
            "LFS batch response exposed a server credential.",
        )
        check(TEST_AUTHORIZATION not in json.dumps(result), "LFS response leaked auth.")
        result = query(
            "/gitadmin/repo.git/git-receive-pack",
            iter([b"a" * 1000000, b"b" * 1500000]),
        )
        check(result["count"] == 2500000, "Chunked upload was truncated or rejected.")
        check(result["authorization"] == TEST_AUTHORIZATION, "Chunked upload lost auth.")

        original = auth_file.read_bytes()
        check(configure("incorrect-password").returncode != 0, "Invalid credentials were accepted.")
        check(auth_file.read_bytes() == original, "Invalid credentials replaced the saved configuration.")
        print(
            "PASS: Git-only auth, unchanged Web/API auth, LFS credential redaction, "
            "Host/HTTPS forwarding, chunked 2.5 MB upload, private credential file, "
            "and invalid-credential rejection."
        )
    finally:
        nginx.terminate()
        try:
            nginx.wait(timeout=5)
        except subprocess.TimeoutExpired:
            nginx.kill()
            nginx.wait()
        server.shutdown()
        server.server_close()


def on_host():
    name = "gitea-gateway-test-" + uuid.uuid4().hex
    try:
        result = subprocess.run(
            [
                "docker", "run", "--rm", "--pull", "never", "-i",
                "--name", name, "--network", "none", "--tmpfs", "/data",
                "--entrypoint", "python3", IMAGE, "-", "--inside-container",
            ],
            input=Path(__file__).read_bytes(),
            timeout=120,
        )
        return result.returncode
    finally:
        subprocess.run(
            ["docker", "rm", "--force", "--volumes", name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )


if __name__ == "__main__":
    if sys.argv[1:] == ["--inside-container"]:
        inside_container()
    elif not sys.argv[1:]:
        sys.exit(on_host())
    else:
        sys.exit("Usage: python3 tests/test-git-gateway.py")
