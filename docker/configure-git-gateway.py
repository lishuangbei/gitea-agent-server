#!/usr/bin/env python3
"""Validate server credentials, then atomically configure the trusted Git gateway.

--auto USER reuses a saved authorization or creates a scoped token with the
local Gitea CLI. This needs no existing account password. Manual input remains
username NUL password NUL on stdin; credentials are never arguments.
The caller reloads Nginx only after this utility succeeds.
"""

import base64
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid


AUTH_DIRECTORY = Path("/data/git-gateway")
BACKEND_USER_URL = "http://127.0.0.1:3001/api/v1/user"


class ConfigurationError(Exception):
    pass


class AuthenticationRejected(ConfigurationError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read_credentials(stream):
    fields = stream.read(65537).split(b"\0")
    if len(fields) != 3 or fields[2] or not fields[0] or not fields[1]:
        raise ConfigurationError("Expected username and password on standard input.")
    if sum(map(len, fields)) > 65534:
        raise ConfigurationError("Credential input is too long.")
    try:
        username = fields[0].decode("utf-8")
        fields[1].decode("utf-8")
    except UnicodeDecodeError:
        raise ConfigurationError("Credentials must be UTF-8 text.") from None
    if len(username) > 255 or ":" in username or any(ord(c) < 32 for c in username):
        raise ConfigurationError("Invalid Gitea username.")
    authorization = "Basic " + base64.b64encode(fields[0] + b":" + fields[1]).decode("ascii")
    return username, authorization


def validate_credentials(username, authorization):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(
        BACKEND_USER_URL,
        headers={"Authorization": authorization, "Accept": "application/json"},
    )
    try:
        with opener.open(request, timeout=30) as response:
            if response.status != 200:
                raise ConfigurationError("Gitea did not accept the server credentials.")
            body = response.read(1048577)
            if len(body) > 1048576:
                raise ConfigurationError("Unexpected Gitea authentication response.")
            identity = json.loads(body)
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise AuthenticationRejected("Gitea did not accept the server credentials.") from None
        raise ConfigurationError("Gitea authentication check failed.") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise ConfigurationError("Could not verify credentials with the local Gitea service.") from None
    if not isinstance(identity, dict) or not isinstance(identity.get("login"), str):
        raise ConfigurationError("Unexpected Gitea authentication response.")
    if identity["login"].casefold() != username.casefold():
        raise AuthenticationRejected("Gitea returned a different account.")
    if identity.get("prohibit_login") or identity.get("must_change_password"):
        raise ConfigurationError("The Gitea account cannot currently perform Git operations.")


def write_configuration(authorization):
    if AUTH_DIRECTORY.is_symlink():
        raise ConfigurationError("Git gateway directory must not be a symbolic link.")
    AUTH_DIRECTORY.mkdir(parents=True, mode=0o700, exist_ok=True)
    if not stat.S_ISDIR(AUTH_DIRECTORY.lstat().st_mode):
        raise ConfigurationError("Git gateway configuration directory is invalid.")
    os.chown(AUTH_DIRECTORY, 0, 0)
    os.chmod(AUTH_DIRECTORY, 0o700)
    temporary_path = None
    try:
        fd, temporary_path = tempfile.mkstemp(prefix=".auth-", dir=AUTH_DIRECTORY)
        with os.fdopen(fd, "w", encoding="ascii") as output:
            os.fchmod(output.fileno(), 0o600)
            os.fchown(output.fileno(), 0, 0)
            # Base64 has no Nginx quoting, interpolation or statement characters.
            # The same variable supplies the LFS JSON response redaction filter.
            output.write(f'set $gateway_authorization "{authorization}";\n')
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, AUTH_DIRECTORY / "auth.conf")
        temporary_path = None
        directory_fd = os.open(AUTH_DIRECTORY, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary_path is not None:
            os.unlink(temporary_path)


def saved_authorization():
    path = AUTH_DIRECTORY / "auth.conf"
    if AUTH_DIRECTORY.is_symlink() or path.is_symlink():
        raise ConfigurationError("Git gateway configuration must not be a symbolic link.")
    if not path.exists():
        return None
    if not path.is_file() or path.stat().st_size > 65536:
        raise ConfigurationError("Invalid saved Git gateway configuration.")
    match = re.fullmatch(
        r'set \$gateway_authorization "(Basic [A-Za-z0-9+/]+=*)";\s*',
        path.read_text(encoding="ascii"),
    )
    if not match:
        raise ConfigurationError("Saved Git gateway configuration was modified; no changes made.")
    return match.group(1)


def require_backend_available():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(BACKEND_USER_URL, timeout=10) as response:
            if response.status != 200:
                raise ConfigurationError("The local Gitea API is not ready; no token was created.")
    except urllib.error.HTTPError as error:
        status = error.code
        error.close()
        if status not in (401, 403):
            raise ConfigurationError("The local Gitea API is not ready; no token was created.") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ConfigurationError("The local Gitea API is unavailable; no token was created.") from None


def configure_automatically(username):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", username):
        raise ConfigurationError("Invalid Gitea username.")
    previous = saved_authorization()
    if previous:
        try:
            validate_credentials(username, previous)
        except AuthenticationRejected:
            pass
        else:
            print("Existing server Git authorization is valid; reusing it.")
            return

    require_backend_available()
    # CLI output contains the only plaintext copy of the newly generated token.
    # Capture it in memory; never echo CLI output or an exception containing it.
    try:
        result = subprocess.run(
            ["su-exec", "git", "gitea", "--config", "/data/gitea/conf/app.ini",
             "admin", "user", "generate-access-token", "--username", username,
             "--token-name", "trusted-git-gateway-" + uuid.uuid4().hex,
             "--scopes", "read:user,write:repository", "--raw"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ConfigurationError("Could not create the server Git token using local Gitea administration.") from None
    token = result.stdout.strip()
    if result.returncode != 0 or not re.fullmatch(r"[0-9a-fA-F]{40}", token):
        raise ConfigurationError("Local Gitea token creation failed; the existing account was not changed.")
    authorization = "Basic " + base64.b64encode(f"{username}:{token}".encode()).decode("ascii")
    validate_credentials(username, authorization)
    write_configuration(authorization)
    print("Server Git token configured automatically; no account password was needed.")


def wait_for_gateway(username):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", username):
        raise ConfigurationError("Invalid Gitea username.")
    # Gitea advertises push-to-create refs without creating a repository. This
    # waits for Nginx's new workers after reload without sending any credentials
    # or creating test data in the user's deployment.
    url = (f"http://127.0.0.1:3000/{username}/gateway-readiness-{uuid.uuid4().hex}.git"
           "/info/refs?service=git-receive-pack")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            with opener.open(url, timeout=2) as response:
                if (response.status == 200 and response.headers.get_content_type()
                        == "application/x-git-receive-pack-advertisement"):
                    print("Git gateway is ready for requests without client credentials.")
                    return
        except urllib.error.HTTPError as error:
            error.close()
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
        time.sleep(0.1)
    raise ConfigurationError("The Git gateway did not become ready after reload; server data was preserved.")


def main():
    if os.geteuid() != 0:
        raise ConfigurationError("Run this utility as root inside the Gitea container.")
    if len(sys.argv) == 3 and sys.argv[1] == "--auto":
        configure_automatically(sys.argv[2])
        return
    if len(sys.argv) == 3 and sys.argv[1] == "--wait-ready":
        wait_for_gateway(sys.argv[2])
        return
    if len(sys.argv) != 1:
        raise ConfigurationError("Use --auto USER, --wait-ready USER, or supply credentials on standard input.")
    username, authorization = read_credentials(sys.stdin.buffer)
    validate_credentials(username, authorization)
    write_configuration(authorization)
    print("Trusted Git access configured; reload the Git gateway to apply it.")


if __name__ == "__main__":
    try:
        main()
    except ConfigurationError as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
    except OSError:
        print("Error: Could not save the private Git gateway configuration.", file=sys.stderr)
        sys.exit(1)
