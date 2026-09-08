"""Authorize a repository deploy key; credentials are supplied only on stdin."""
import base64
import http.client
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request


class ClientError(Exception):
    def __init__(self, message, exit_code=1):
        super().__init__(message)
        self.exit_code = exit_code


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def key_identity(value):
    """Compare the key material, ignoring its optional trailing comment."""
    if not isinstance(value, str) or "\n" in value.strip() or "\r" in value.strip():
        raise ClientError("Invalid SSH public key.")
    parts = value.split()
    if len(parts) < 2:
        raise ClientError("Invalid SSH public key.")
    try:
        blob = base64.b64decode(parts[1], validate=True)
        name_length = int.from_bytes(blob[:4], "big")
        if len(blob) < 4 or name_length > len(blob) - 4:
            raise ValueError
        if blob[4:4 + name_length].decode("ascii") != parts[0]:
            raise ValueError
    except (ValueError, UnicodeError):
        raise ClientError("Invalid SSH public key.") from None
    return parts[0], blob


def key_id(value):
    if type(value) is not int or value <= 0:
        raise ClientError("Gitea returned an invalid deploy key ID.")
    return value


def main():
    raw = sys.stdin.buffer.read(65537)
    fields = raw.split(b"\0")
    if len(raw) > 65536 or len(fields) != 7 or fields[-1] != b"":
        raise ClientError("Invalid request input.")
    try:
        mode, repo_name, title, public_key, username, password = (
            value.decode("utf-8") for value in fields[:-1]
        )
    except UnicodeError:
        raise ClientError("Invalid request input.") from None
    if mode not in {"check", "authorize"}:
        raise ClientError("Use check or authorize mode.")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo_name):
        raise ClientError("Use an existing repository in OWNER/NAME format.")
    if any(part in {".", ".."} for part in repo_name.split("/")):
        raise ClientError("Invalid repository name.")
    if not username or ":" in username or not password:
        raise ClientError("Gitea username and password are required.")
    if mode == "authorize":
        if not title.strip() or any(ord(char) < 32 or ord(char) == 127 for char in title):
            raise ClientError("A nonempty deploy key title without control characters is required.")
        identity = key_identity(public_key)

    credentials = base64.b64encode(f"{username}:{password}".encode()).decode()
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), NoRedirect()
    )

    def request(method, path, payload=None):
        headers = {"Authorization": "Basic " + credentials, "Accept": "application/json"}
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode()
        req = urllib.request.Request(
            "http://127.0.0.1:3000/api/v1" + path,
            data=data, headers=headers, method=method,
        )
        try:
            with opener.open(req, timeout=30) as response:
                if response.status != (201 if method == "POST" else 200):
                    raise ClientError("Gitea returned an unexpected response status.")
                body = response.read(8 * 1024 * 1024 + 1)
                if len(body) > 8 * 1024 * 1024:
                    raise ClientError("Gitea returned an oversized response.")
                try:
                    return json.loads(body)
                except (ValueError, UnicodeError):
                    raise ClientError("Gitea returned an invalid JSON response.") from None
        except urllib.error.HTTPError as exc:
            # Never echo a backend response or exception that may contain credentials.
            status = exc.code
            exc.close()
            if status == 401:
                raise ClientError("Gitea authentication failed.", 3) from None
            if status == 403:
                raise ClientError("Gitea denied access; repository administrator permission is required to manage deploy keys.") from None
            if status == 404:
                raise ClientError("The repository or deploy key was not found or is inaccessible.") from None
            raise ClientError(f"Gitea rejected the request (HTTP {status}).") from None
        except (OSError, urllib.error.URLError, http.client.HTTPException):
            if method == "POST":
                raise ClientError("Deploy key creation could not be confirmed; rerun to check whether it completed.") from None
            raise ClientError("Could not reach the local Gitea API.") from None

    user = request("GET", "/user")
    if not isinstance(user, dict) or not isinstance(user.get("login"), str):
        raise ClientError("Gitea did not return an authenticated user.")
    repo_path = "/repos/" + "/".join(
        urllib.parse.quote(part, safe="") for part in repo_name.split("/")
    )
    repo = request("GET", repo_path)
    if not isinstance(repo, dict):
        raise ClientError("Gitea returned invalid repository information.")
    if repo.get("mirror") is not False:
        raise ClientError("Use an ordinary repository; pull mirrors do not accept pushes.")
    if repo.get("archived") is not False:
        raise ClientError("Archived repositories do not accept pushes.")
    permissions = repo.get("permissions")
    if not isinstance(permissions, dict) or permissions.get("push") is not True:
        raise ClientError("This Gitea account lacks repository write permission.")
    # Both listing and creating deploy keys require repository admin permission.
    if permissions.get("admin") is not True:
        raise ClientError("Repository administrator permission is required to authorize deploy keys.")
    if mode == "check":
        print("Repository write and deploy key management access verified.")
        return

    def verify_key(value, expected_id=None, expected_title=None):
        if not isinstance(value, dict) or key_identity(value.get("key")) != identity:
            raise ClientError("Gitea returned a different deploy key; authorization could not be verified.")
        result_id = key_id(value.get("id"))
        if expected_id is not None and result_id != expected_id:
            raise ClientError("Gitea returned a different deploy key ID.")
        if expected_title is not None and value.get("title") != expected_title:
            raise ClientError("Gitea returned a different deploy key title.")
        if value.get("read_only") is not False:
            raise ClientError("The matching deploy key is read-only or its write access could not be verified; no permissions were changed.")
        return result_id

    matched = None
    seen_ids = set()
    page = 1
    while True:
        keys = request("GET", f"{repo_path}/keys?limit=100&page={page}")
        if not isinstance(keys, list):
            raise ClientError("Gitea returned an invalid deploy key list.")
        if not keys:
            break
        for existing in keys:
            if not isinstance(existing, dict):
                raise ClientError("Gitea returned invalid deploy key information.")
            existing_id = key_id(existing.get("id"))
            if existing_id in seen_ids:
                raise ClientError("The deploy key list changed or pagination failed; rerun before authorizing.")
            seen_ids.add(existing_id)
            same_key = key_identity(existing.get("key")) == identity
            if existing.get("title") == title and not same_key:
                raise ClientError("The deploy key title is already used by a different key; choose another title.")
            if same_key:
                verify_key(existing)
                matched = existing_id
        # The server may cap limit below 100; only an empty page ends the list.
        page += 1

    if matched is not None:
        verify_key(request("GET", f"{repo_path}/keys/{matched}"), expected_id=matched)
        print(f"Writable deploy key already authorized (id {matched}).")
        return
    created = request("POST", repo_path + "/keys", {
        "title": title, "key": public_key.strip(), "read_only": False,
    })
    created_id = verify_key(created, expected_title=title)
    verify_key(
        request("GET", f"{repo_path}/keys/{created_id}"),
        expected_id=created_id, expected_title=title,
    )
    print(f"Writable deploy key authorized (id {created_id}).")


if __name__ == "__main__":
    try:
        main()
    except ClientError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(exc.exit_code)
    except Exception:
        # A malformed server response must not expose secrets in a traceback.
        print("ERROR: The local Gitea request could not be completed.", file=sys.stderr)
        sys.exit(1)
