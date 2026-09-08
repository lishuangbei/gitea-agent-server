# Git gateway regression test

After building `gitea-agent-server:local`, run on the Docker host:

```bash
python3 tests/test-git-gateway.py
```

Requires host Python 3 and Docker. The test uses the gateway configuration and
utility installed in that image, with a mock Gitea backend and fake credentials.
It creates a uniquely named temporary container with networking disabled, no
published ports, and temporary `/data` storage, then removes it. It does not build
an image, start Gitea, or use any existing deployment containers or volumes.

Automatic credential tests replace only the local Gitea token command with a
mock; API validation and credential file handling run normally against the mock
backend. They cover missing configuration, reuse of valid credentials, replacement
of rejected credentials, and API outages without creating extra tokens. No real
account password or token is used.
