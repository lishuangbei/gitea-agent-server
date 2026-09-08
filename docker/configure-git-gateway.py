#!/usr/bin/env python3
"""Validate server credentials, then atomically configure the trusted Git gateway.

Input is username NUL password NUL on stdin; credentials are never arguments.
The caller reloads Nginx only after this utility succeeds.
"""

import base64
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import urllib.error
import urllib.request


AUTH_DIRECTORY = Path("/data/git-gateway")
BACKEND_USER_URL = "http://127.0.0.1:3001/api/v1/user"


class ConfigurationError(Exception):
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
            raise ConfigurationError("Gitea did not accept the server credentials.") from None
        raise ConfigurationError("Gitea authentication check failed.") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise ConfigurationError("Could not verify credentials with the local Gitea service.") from None
    if not isinstance(identity, dict) or not isinstance(identity.get("login"), str):
        raise ConfigurationError("Unexpected Gitea authentication response.")
    if identity["login"].casefold() != username.casefold():
        raise ConfigurationError("Gitea returned a different account.")
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


def main():
    if len(sys.argv) != 1:
        raise ConfigurationError("Supply credentials on standard input, without arguments.")
    if os.geteuid() != 0:
        raise ConfigurationError("Run this utility as root inside the Gitea container.")
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
