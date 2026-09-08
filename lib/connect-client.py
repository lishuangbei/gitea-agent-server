"""Provision a Docker Git client. Host needs Python 3; client only Git/OpenSSH/sh."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys


class SetupError(Exception):
    pass


def run(args, data=None, ok=(0,), timeout=60):
    p = subprocess.run(args, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       text=True, timeout=timeout)
    if p.returncode not in ok:
        raise SetupError((p.stderr or p.stdout or "Command failed").strip())
    return p


def output_line(value):
    return value[:-1] if value.endswith("\n") else value


def main():
    client, project, target = sys.argv[1:]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", client):
        raise SetupError("Invalid client container name or ID")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", target):
        raise SetupError("Use OWNER/REPO without a .git suffix")
    if not project.startswith("/") or any(c in project for c in "\n\r\0"):
        raise SetupError("Supply an absolute project path inside the client container")
    auth = sys.stdin.read().split("\0")
    if len(auth) != 3 or auth[-1]:
        raise SetupError("Invalid provisioning credentials")
    username, password = auth[:2]
    info = json.loads(run(["docker", "inspect", client]).stdout)[0]
    server = json.loads(run(["docker", "inspect", "git-server"]).stdout)[0]
    if info["Id"] == server["Id"]:
        raise SetupError("This helper is for other client containers, not git-server itself")
    if not info["State"]["Running"]:
        raise SetupError("Start the client container first")
    if "git-net" not in server["NetworkSettings"]["Networks"]:
        raise SetupError("git-server is not connected to git-net")

    exec_prefix = ["docker", "exec", "-i"]
    client_user = os.environ.get("CLIENT_USER")
    if client_user:
        exec_prefix += ["--user", client_user]
        # Docker exec --user does not update HOME by itself.
        home = os.environ.get("CLIENT_HOME") or output_line(run(
            exec_prefix + [client, "sh", "-ec",
                           'uid=$(id -u); awk -F: -v uid="$uid" \'$3 == uid {print $6; exit}\' /etc/passwd']
        ).stdout)
        if not home.startswith("/") or any(c in home for c in "\n\r\0"):
            raise SetupError("Cannot resolve CLIENT_USER home; provide CLIENT_HOME")
        exec_prefix += ["--env", "HOME=" + home]
    exec_prefix += ["--workdir", project, client]

    def cx(args, data=None, ok=(0,)):
        return run(exec_prefix + args, data=data, ok=ok)

    def shell(code, *args, data=None, ok=(0,)):
        return cx(["sh", "-eu", "-c", code, "client-setup", *args], data=data, ok=ok)

    def git(*args, ok=(0,)):
        return cx(["git", *args], ok=ok)

    shell('command -v git >/dev/null; command -v ssh >/dev/null; command -v ssh-keygen >/dev/null; '
          '[ -z "${GIT_SSH_COMMAND:-}${GIT_SSH:-}${GIT_SSH_VARIANT:-}" ] || '
          '{ echo "Client SSH environment overrides must be resolved first" >&2; exit 1; }')
    version = cx(["ssh", "-V"])
    if "OpenSSH" not in version.stderr + version.stdout:
        raise SetupError("The client needs OpenSSH")
    uid = cx(["id", "-u"]).stdout.strip()
    common_dir = output_line(shell('p=$(git rev-parse --git-common-dir); cd "$p"; pwd -P').stdout)
    if any(c in common_dir for c in "\n\r\0") or "${" in common_dir:
        raise SetupError("Git directory contains characters unsupported by SSH paths")
    def require_persistent(path):
        mounts = sorted(
            [m for m in info["Mounts"] if PurePosixPath(m["Destination"]) in
             [PurePosixPath(path), *PurePosixPath(path).parents]],
            key=lambda m: len(m["Destination"]), reverse=True,
        )
        if not mounts or mounts[0]["Type"] not in ("volume", "bind") or not mounts[0]["RW"]:
            raise SetupError("The Git/SSH directory is not in a writable persistent mount. Mount the project first; no credentials were created.")

    require_persistent(common_dir)

    canonical_name = info["Name"].lstrip("/")
    identity = {"client": canonical_name, "uid": uid, "repository": target, "version": 1}
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:20]
    alias = "gitea-" + digest
    access_root = common_dir + "/.gitea-access"
    directory = access_root + "/" + digest
    require_persistent(directory)
    wrapper_path = directory + "/ssh-wrapper"
    # Resolve at invocation time: a bind-mounted checkout can have a different
    # absolute path on its host. Other remotes must still find this passthrough.
    ssh_command = 'sh "$(git rev-parse --git-common-dir)/.gitea-access/' + digest + '/ssh-wrapper"'
    remote_url = f"git@{alias}:{target}.git"

    previous_ssh = git("config", "--get", "core.sshCommand", ok=(0, 1)).stdout.strip()
    if previous_ssh and previous_ssh != ssh_command:
        raise SetupError("An existing core.sshCommand is configured; preserve it and resolve the conflict first")
    variant = git("config", "--get", "ssh.variant", ok=(0, 1)).stdout.strip()
    if variant not in ("", "ssh"):
        raise SetupError("An existing non-OpenSSH ssh.variant is configured")
    if git("config", "--bool", "--get", "remote.gitea.mirror", ok=(0, 1)).stdout.strip() == "true":
        raise SetupError("The existing gitea remote has mirror pushes enabled; no changes made")
    old_urls = git("config", "--get-all", "remote.gitea.url", ok=(0, 1)).stdout.splitlines()
    old_push = git("config", "--get-all", "remote.gitea.pushurl", ok=(0, 1)).stdout.splitlines()
    allowed_urls = {remote_url, f"git@git-server:{target}.git"}
    for suffix in ("", ".git"):
        allowed_urls.update({f"http://git-server:3000/{target}{suffix}",
                             f"ssh://git@git-server/{target}{suffix}",
                             f"ssh://git@git-server:22/{target}{suffix}"})
    if any(len(urls) > 1 or any(u not in allowed_urls for u in urls) for urls in (old_urls, old_push)):
        raise SetupError("Existing gitea remote points elsewhere or has multiple URLs; no changes made")

    api_source = Path(__file__).with_name("client-key.py").read_text()

    def api(mode, title="", key=""):
        payload = "\0".join((mode, target, title, key, username, password, ""))
        return run(["docker", "exec", "-i", "--user", "1001:1001", "git-server",
                    "python3", "-c", api_source], data=payload)

    api("check")
    public_host = run(["docker", "exec", "git-server", "cat",
                       "/data/ssh/ssh_host_ed25519_key.pub"]).stdout.split()
    if len(public_host) < 2 or public_host[0] != "ssh-ed25519":
        raise SetupError("No valid trusted Ed25519 SSH host key found in git-server")
    known_hosts = "git-server " + " ".join(public_host[:2]) + "\n"

    # This directory lives inside .git, never in tracked project files.
    manifest = json.dumps(identity, sort_keys=True) + "\n"
    shell('for p in "$1" "$2"; do [ ! -L "$p" ] || exit 1; done; '
          'umask 077; mkdir -p "$1"; '
          'if [ -d "$2" ] && [ -z "$(ls -A "$2")" ]; then rmdir "$2"; fi; '
          'if [ -e "$2" ]; then [ -f "$2/identity.json" ] && [ ! -L "$2/identity.json" ]; '
          'else t=$(mktemp -d "$1/.new.XXXXXX"); trap \'rm -rf "$t"\' EXIT; '
          'cat > "$t/identity.json"; mv -T "$t" "$2"; fi; chmod 700 "$1" "$2"',
          access_root, directory, data=manifest)

    def existing(path):
        return shell('[ ! -L "$1" ] || { echo "Refusing a symlink" >&2; exit 2; }; '
                     '[ ! -e "$1" ] || cat "$1"', path).stdout

    def write(path, content):
        shell('[ ! -L "$1" ] || exit 1; umask 077; t=$(mktemp "$1.tmp.XXXXXX"); '
              'trap \'rm -f "$t"\' EXIT; cat > "$t"; chmod 600 "$t"; mv "$t" "$1"',
              path, data=content)

    if existing(directory + "/identity.json") not in ("", manifest):
        raise SetupError("Existing SSH identity belongs to a different client or repository")
    write(directory + "/identity.json", manifest)
    private_key = directory + "/id_ed25519"
    shell('[ ! -L "$1" ] && [ ! -L "$1.pub" ] || exit 1; '
          'umask 077; if [ ! -e "$1" ]; then '
          '[ ! -e "$1.pub" ] || { echo "Private key missing; refusing to replace existing public key" >&2; exit 1; }; '
          'ssh-keygen -q -t ed25519 -N "" -C "$2" -f "$1"; fi; chmod 600 "$1"',
          private_key, "gitea-client-" + digest)
    public_key = cx(["ssh-keygen", "-y", "-P", "", "-f", private_key]).stdout.strip()
    if not public_key.startswith("ssh-ed25519 "):
        raise SetupError("Existing client key is not Ed25519")

    def ssh_path(path):
        return '"' + path.replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"') + '"'

    ssh_config = f"""Host {alias}
    HostName git-server
    User git
    Port 22
    IdentityFile {ssh_path(private_key)}
    IdentitiesOnly yes
    IdentityAgent none
    BatchMode yes
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    StrictHostKeyChecking yes
    HostKeyAlias git-server
    UserKnownHostsFile {ssh_path(directory + '/known_hosts')}
    GlobalKnownHostsFile /dev/null
    ConnectTimeout 15
"""
    wrapper = f"""#!/bin/sh
# Only this generated alias uses the provisioned key; other remotes use normal SSH.
destination=
skip=false
for argument do
  if [ "$skip" = true ]; then skip=false; continue; fi
  case "$argument" in
    -B|-b|-c|-D|-E|-e|-F|-I|-i|-J|-L|-l|-m|-O|-o|-P|-p|-Q|-R|-S|-W|-w) skip=true ;;
    -*) ;;
    *) destination=$argument; break ;;
  esac
done
case "$destination" in
  {alias}|git@{alias})
    directory=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
    exec ssh -F "$directory/ssh_config" "$@" ;;
  *) exec ssh "$@" ;;
esac
"""
    for path, content in ((wrapper_path, wrapper), (directory + "/ssh_config", ssh_config)):
        if existing(path) not in ("", content):
            raise SetupError("Existing generated SSH configuration was modified; refusing to overwrite it")
        write(path, content)
    write(directory + "/known_hosts", known_hosts)

    # Separate checkouts can have the same client/repository identity but distinct
    # private keys. Derive the API title from the public key, not the SSH alias.
    title = "docker-client-" + hashlib.sha256(public_key.encode()).hexdigest()[:24]
    api("authorize", title, public_key)
    if "git-net" not in info["NetworkSettings"]["Networks"]:
        run(["docker", "network", "connect", "git-net", client])
    # Validate both Git transports before persisting remote changes. No commits sent.
    git("-c", "core.sshCommand=" + ssh_command, "ls-remote", remote_url)
    cx(["sh", wrapper_path, "git@" + alias, "git-receive-pack '" + target + ".git'"], data="0000")
    git("config", "--local", "core.sshCommand", ssh_command)
    if old_urls:
        git("remote", "set-url", "gitea", remote_url)
    else:
        git("remote", "add", "gitea", remote_url)
    if old_push:
        git("remote", "set-url", "--push", "gitea", remote_url)
    print(f"Configured client: {canonical_name} (UID {uid})")
    print(f"Gitea repository: {target}")
    print(f"Remote: {remote_url} (SSH alias routes to git-server:22)")
    print("Non-interactive read and write transport authentication passed; no commits were pushed.")
    print("Inside this project, use: git fetch gitea / git push gitea HEAD")
    print("Keep this project volume and declare external network git-net in the client's Compose file.")


if __name__ == "__main__":
    try:
        main()
    except (SetupError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        # API errors already suppress credential-bearing responses; never print argv.
        message = str(exc) if not isinstance(exc, subprocess.TimeoutExpired) else "A command timed out"
        print("ERROR: " + message, file=sys.stderr)
        sys.exit(1)
