"""Container-side API client; all credentials arrive on stdin, never argv."""
import base64
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # The local API never needs redirects; do not forward Basic credentials.
        return None


def main():
    fields = sys.stdin.buffer.read().split(b"\0")
    if len(fields) != 7 or fields[-1] != b"":
        raise ValueError("Invalid request input")
    mode, source, name, github_token, username, password = (
        value.decode("utf-8") for value in fields[:-1]
    )
    credentials = base64.b64encode(f"{username}:{password}".encode()).decode()
    # Calls stay inside the Gitea container, independent of host port/Tailscale.
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), NoRedirect()
    )

    def request(method, path, payload=None, allow_missing=False):
        headers = {"Authorization": "Basic " + credentials}
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode()
        req = urllib.request.Request(
            "http://127.0.0.1:3000/api/v1" + path,
            data=data, headers=headers, method=method,
        )
        try:
            with opener.open(req, timeout=3600) as response:
                body = response.read()
                return json.loads(body) if body else None
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and allow_missing:
                return None
            if exc.code == 401:
                print("Gitea authentication failed.", file=sys.stderr)
                raise SystemExit(3)
            # Backend errors can include a clone URL; strip credential material.
            message = exc.read().decode(errors="replace")
            for secret in (github_token, password, credentials):
                if secret:
                    variants = {secret, urllib.parse.quote(secret, safe="")}
                    variants.update(
                        json.dumps(secret, ensure_ascii=ascii_only)[1:-1]
                        for ascii_only in (True, False)
                    )
                    for variant in sorted(variants, key=len, reverse=True):
                        message = message.replace(variant, "[redacted]")
            message = re.sub(r"https?://[^\s/]+@", "https://[redacted]@", message)
            raise ValueError(f"Gitea HTTP {exc.code}: {message[:1200]}") from None

    user = request("GET", "/user")
    if mode == "sync":
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", name):
            raise ValueError("Use GITEA_OWNER/MIRROR_NAME")
        path = "/repos/" + name
        repo = request("GET", path)
        if not repo.get("mirror"):
            raise ValueError("This repository is not a pull mirror; no changes made.")
        request("POST", path + "/mirror-sync")
        print("Mirror sync queued. It completes asynchronously.")
        return

    if mode != "create" or not re.fullmatch(
        r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/?", source
    ):
        raise ValueError("Use an HTTPS github.com repository URL")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise ValueError("Invalid mirror name")
    owner = user["login"]
    path = f"/repos/{urllib.parse.quote(owner, safe='')}/{urllib.parse.quote(name, safe='')}"
    if request("GET", path, allow_missing=True) is not None:
        raise ValueError("A repository already has this name. Choose a new name, or use --sync for an existing mirror.")
    payload = {
        "clone_addr": source,
        "repo_owner": owner,
        "repo_name": name,
        "service": "git",
        "private": True,
        "mirror": True,
    }
    if github_token:
        payload["auth_username"] = "x-access-token"
        payload["auth_password"] = github_token
    print("Importing GitHub repository as a private pull mirror...", flush=True)
    repo = request("POST", "/repos/migrate", payload)
    if not repo.get("mirror") or not repo.get("private"):
        raise ValueError("The server did not return a private pull mirror; inspect its repository settings.")
    print("Private pull mirror created successfully.")
    print(f"Docker clone URL: http://git-server:3000/{owner}/{name}.git")
    print(f"Gitea URL: {repo['html_url']}")
    if "mirror_interval" in repo:
        print(f"Sync interval: {repo['mirror_interval']}")
    print(f"Sync now from host: ./mirror-github.sh --sync {owner}/{name}")
    print("Clone/pull use Gitea credentials. Push code changes to GitHub.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, urllib.error.URLError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
