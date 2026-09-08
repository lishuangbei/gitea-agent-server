# Gitea Agent Server

在**同一个 `git-server` Docker 容器**中运行 Gitea、Claude Code、DeepSeek Harness（`dsh`）和 Tailscale。Gitea 提供私有 Git 仓库；两个代码工具通过独立的 `agent` 用户使用；Tailscale 在容器内运行，让其他 tailnet 机器访问 Git 服务。

默认用法是让自己的 Docker 容器和 tailnet 机器直接 clone/pull/push，**客户端无需密码、Token 或 SSH 密钥**。已有本机仓库第一次 push 到 `gitadmin/仓库名.git` 时自动创建普通私有仓库，不需要先打开网页。原有 GitHub remote 可以保留，按需分别推送到两个服务器。GitHub → Gitea 的只读拉取镜像是可选功能。

## 快速开始

需要 Docker、Docker Compose v2、Bash，以及下载镜像和软件包的网络连接。

```bash
git clone https://github.com/lishuangbei/gitea-agent-server.git
cd gitea-agent-server
./setup-gitea.sh
```

脚本构建镜像、启动服务并创建持久化卷。首次启动 Gitea 不需要 GitHub、Claude、DeepSeek 或 Tailscale 凭据；Tailscale 可以稍后授权。宿主机无需安装或运行 Tailscale，脚本也不修改宿主机已有的 Tailscale 配置。

如果已有其他容器需要访问 Git，可传入容器名：

```bash
./setup-gitea.sh worker-1 worker-2
```

这些容器会加入 `git-net`，之后直接使用仓库 HTTP 地址。无需运行 `connect-client.sh`。客户端重建后的网络配置示例见下文。

管理员为 `gitadmin`，初始密码保存在**脚本输出的状态目录**中的 `admin-password.txt`。脚本仅在服务端使用该凭据配置统一 Git 身份，不将管理员密码分发给客户端。已有管理员密码不会被重置；如果后来改过密码且状态文件未更新，重新部署时需通过 `GITEA_PASSWORD` 环境变量提供当前密码。

## 把已有代码推送到 Gitea

例如已经在客户端容器的 `/workspace/splitx` 从 GitHub clone 了项目，先让该容器加入 `git-net`，然后在**客户端容器内**执行：

```bash
cd /workspace/splitx
git remote -v
git remote add gitea http://git-server:3000/gitadmin/splitx.git
git push gitea HEAD
```

第一次 push 会自动创建 `gitadmin/splitx` 普通私有仓库，并上传当前分支，全程无需输入 Gitea 凭据。目标已存在时正常推送，不覆盖它原有的历史；拉取镜像和归档仓库不能用于写入。已有名为 `gitea` 的 remote 时先核对目标，再用 `git remote set-url gitea URL` 更新地址。原 GitHub `origin` 保留；向 Gitea push 不会自动更新 GitHub。

首次推送包含 Git LFS 文件时，需先由管理员通过 Gitea API 创建空仓库，再按上述方式免凭据推送 Git 和 LFS 内容；LFS 上传发生在普通 Git 推送之前，因此这种首次推送不能依靠 push 自动建库。

其他已接入网络的容器可直接克隆：

```bash
git clone http://git-server:3000/gitadmin/splitx.git
```

M1 Max 等 tailnet 机器使用同样的 Git 命令，将 URL 改成 `https://GITEA.TAILNET.ts.net/gitadmin/splitx.git`，域名取自 `./tailnet.sh login` 输出。在 Docker 宿主机上使用 `http://127.0.0.1:3000/gitadmin/splitx.git`。

`/workspace/splitx` 是工作副本的磁盘路径；Git URL 中的 `gitadmin/splitx` 是 Gitea 的账号和仓库名。Gitea 不会自动把 `/workspace` 下的 Git 目录发布为仓库，第一次 push 才会完成创建和上传。

## 让其他 Docker 容器接入网络

客户端需要 Git，并与 Gitea 位于**同一台 Docker host**。在 host 上执行部署脚本时传入容器名即可自动连接网络：

```bash
./setup-gitea.sh worker-1 worker-2
```

服务已经部署后，也可直接在 host 上为一个运行中的容器加入网络，无需重建 Gitea：

```bash
docker ps --format 'table {{.Names}}\t{{.Image}}'
docker network connect git-net worker-1
```

将 `worker-1` 替换为实际容器名。这只是让两个容器能够通信，不涉及仓库授权或代码上传。在 `git-server` 容器自身操作 Git 时无需再加入网络。客户端不需要 Python、OpenSSH 或认证初始化脚本；建议将工作副本放在持久化数据卷中，以便重建容器时保留代码。

为让客户端重建后仍加入 `git-net`，把下面网络片段合并到客户端已有的 Compose 文件，将 `worker-1` 改为实际服务名，并保留该服务原有的其他网络：

```yaml
services:
  worker-1:
    networks:
      - git-net

networks:
  git-net:
    external: true
    name: git-net
```

## 访问地址和统一 Git 身份

| 访问位置 | Git 地址 |
| --- | --- |
| `git-net` 中的客户端 | `http://git-server:3000/gitadmin/REPO.git` |
| Docker 宿主机 | `http://127.0.0.1:3000/gitadmin/REPO.git` |
| Tailnet 机器 | `https://GITEA.TAILNET.ts.net/gitadmin/REPO.git` |

`REPO` 和域名均为占位符。默认入口在服务端将 Git 和 Git LFS 请求统一作为 `gitadmin` 处理，客户端无需凭据；网页和普通 API 仍需正常登录。默认网页链接使用 Docker 内部名称 `git-server`；配置 tailnet 后使用实际 HTTPS 地址。

这是为自己的可信网络提供的统一 Git 入口：**任何能连到该入口的客户端，都可以读取和写入 `gitadmin` 有权访问的仓库**，Git 操作记录使用这一身份。仓库标记为“私有”和网页登录不会限制这个入口的 Git 访问；tailnet 客户端同样无需额外 Git 认证。脚本保持宿主机端口仅绑定 `127.0.0.1:3000`，不新增公网端口映射；能否连接由 Docker 网络和现有 tailnet 访问规则决定。

## 可选：保留原来的 SSH 接入方式

`connect-client.sh` 仍可用于为指定 Docker 客户端配置单个仓库的 SSH deploy key，但默认 HTTP/HTTPS 用法不需要它。选择 SSH 时，目标必须已存在，host 需 Python 3，客户端需 Git/OpenSSH，项目的 Git 目录需位于可写持久化卷：

```bash
./connect-client.sh worker-1 /workspace/my-project gitadmin/my-project

# 若平时以 agent 用户操作 Git
CLIENT_USER=agent ./connect-client.sh worker-1 /workspace/my-project gitadmin/my-project
```

此脚本配置 `gitea` remote、专用密钥和可信服务器公钥，保留 `origin`，不会上传提交。密钥保存在 Git common directory 的 `.gitea-access` 中；重建客户端时保留数据卷、原挂载路径和执行用户。数字 UID 没有 `/etc/passwd` 条目时需额外指定 `CLIENT_HOME`。SSH 授权不影响上文 HTTP/HTTPS 入口的统一访问行为。

## 容器内 Tailscale

以下辅助脚本在宿主机终端执行，**所有 Tailscale 命令都在 `git-server` 容器内部运行**：

```bash
./tailnet.sh login
```

首次节点名默认为 `gitea`，也可在首次登录时自定义：

```bash
GITEA_TS_HOSTNAME=my-gitea ./tailnet.sh login
```

脚本打印授权链接并等待最多 5 分钟。服务器无需 GUI，可在另一台设备打开链接授权。已有 auth key 时可隐藏输入：

```bash
./tailnet.sh login --auth-key
```

密钥通过 stdin 传入容器，不读取宿主机的 auth key 环境变量，也不保存进部署仓库。已有 key 只能省去节点的网页登录；若 tailnet 尚未启用 HTTPS，仍可能需要在另一台设备打开提示链接并启用。

连接完成后，脚本配置容器内 Serve，将 HTTPS 转发到同容器的 `127.0.0.1:3000`，自动保存实际 `ROOT_URL` 到 `compose.tailnet.yaml`，并重建 Gitea 容器使配置生效。已有连接时可刷新入口或查看状态：

```bash
./tailnet.sh serve
./tailnet.sh status
```

Tailscale 使用 userspace 模式，由容器现有的 s6 监督进程管理；不需要 `NET_ADMIN` 或 `/dev/net/tun`。节点身份、证书和后台 Serve 配置保存在 `tailscale-state` 卷的 `/var/lib/tailscale`，目录归 root 所有、权限为 `0700`，重建后恢复。不要把该卷或其中的节点密钥复制给另一个同时运行的实例。

Docker 客户端继续使用内部地址，两种入口访问同一个 Gitea。容器拥有独立的 Tailscale 节点和 Serve 配置；辅助脚本发现容器内已有冲突的 Serve/Funnel 配置时会停止，供你检查。

**Serve 并不把 tailnet 访问自动限制为 443。** Userspace 网络栈还可能把发往该节点其他端口的连接转到容器回环地址，包括 Gitea 的 `22`、`3000`，以及以后启动的其他服务。按实际需求配置 tailnet ACL/grants；如果只需要 HTTPS，应只允许所需客户端访问该节点的 `443`，并检查其他规则没有额外放行。上文 HTTP/HTTPS Git 入口使用统一的 `gitadmin` 身份；网页、普通 API 和直接 SSH 仍按各自的认证规则处理。

## 使用 Claude Code 和 dsh

```bash
# 进入同一容器内的 Bash
./agent-shell.sh

# 启动 Claude Code
./agent-shell.sh claude

# 查看 DeepSeek Harness 的使用帮助
./agent-shell.sh dsh --help
./agent-shell.sh dsh --profile headless --help
```

这些命令以独立的 `agent` 用户（UID `1001`）运行，默认目录为 `/workspace`。Gitea 使用自己的 `git` 用户。`/home/agent` 和 `/workspace` 分别持久化，重建后保留工具设置、保存的登录状态和工作副本。

首次使用时分别完成工具授权；镜像和仓库不附带凭据，也不包含服务额度。未验证付费 API 调用。使用 dsh 时可先进入 `./agent-shell.sh`，在容器终端输入：

```bash
read -rsp 'DeepSeek API key: ' DEEPSEEK_API_KEY
printf '\n'
export DEEPSEEK_API_KEY
dsh --profile headless "概述当前工作目录中的项目"
```

密钥输入不回显，也不作为命令文本进入 shell 历史。这个环境变量仅在当前 shell 有效；由工具保存到 home 的设置和凭据才随数据卷保留。`agent-shell.sh` 不自动传入宿主机 API key 环境变量。

当前 dsh 固定版本没有内置 `tui` profile；本仓库提供 headless 用法，没有 Web 启动或 Docker 端口映射辅助脚本。手动启动回环地址上的 Web 服务时，同样需要考虑上文的 tailnet 端口访问规则。

请把工作副本克隆到 `/workspace`，不要直接编辑 `/data` 中的 Gitea 服务数据。

## 可选：GitHub 只读拉取镜像

仅在需要定期从 GitHub 拉取仓库时使用：

```bash
./mirror-github.sh https://github.com/OWNER/REPO.git
./mirror-github.sh https://github.com/OWNER/REPO.git my-mirror
./mirror-github.sh --sync gitadmin/my-mirror
```

脚本复用宿主机的 `GH_TOKEN`、`GITHUB_TOKEN` 或已登录的 `gh`，否则隐藏提示输入 Token。私有源仓库需要 GitHub 授权；只读取代码时可限定目标仓库并授予 Contents 只读权限。Gitea 保存该凭据用于定期同步。

此功能通过容器内 API 创建**私有、只读**的拉取镜像。同名仓库不会被覆盖，不要预先创建普通空仓库。镜像只接受 GitHub → Gitea 的同步，不能向它 push；默认不迁移 Issues、PR 或 Git LFS 文件。`--sync` 返回只表示任务已排队，需随后核对实际同步结果。

## 管理和更新

```bash
./manage.sh ps
./manage.sh logs --tail=100
```

管理脚本加载基础配置及已生成的 harness、tailnet 配置。`compose.harness.yaml` 包含构建配置和新增数据卷，不要只加载基础 `compose.yaml` 来重建服务。

升级已有部署以启用默认免凭据 Git 入口，在 Docker host 的部署仓库目录执行：

```bash
git pull --ff-only
./setup-gitea.sh worker-1 worker-2
```

把参数替换为需要接入的现有客户端容器名；也可省略参数。镜像必须重新构建并重建服务，新脚本会完成这一步，保留已有仓库、账号、工作副本和 Tailscale 身份。已经登录的 Tailscale 无需重新授权；尚未登录时再执行 `./tailnet.sh login`。

脚本通过已有容器的 Compose 标签找到原状态目录，并保存到本地 `.gitea-state-dir`；也可显式指定原 `GITEA_DIR`。必须沿用原状态目录。构建完成后才替换容器，保留已有配置、管理员和数据卷。已保存的外部 URL 不会因省略环境变量而消失；`tailnet.sh login` 或 `serve` 会更新为容器节点的实际地址。

| Compose 卷 | 容器内路径 | 内容 |
| --- | --- | --- |
| `git-data` | `/data` | 仓库、数据库、Gitea 配置 |
| `agent-home` | `/home/agent` | 工具设置、保存的凭据和会话 |
| `agent-workspace` | `/workspace` | 工作副本 |
| `tailscale-state` | `/var/lib/tailscale` | 节点身份、证书、Serve 配置 |

项目名固定为 `local-git-server`，原 Gitea 数据卷仍为 `local-git-server_git-data`。升级前备份状态目录及以上数据卷，不要删除卷或执行 `down -v`。密码、Token、节点身份、备份和本地状态均不应提交到 Git。

## 默认版本

| 组件 | 版本 |
| --- | --- |
| Gitea | `1.27.3` |
| Claude Code | `2.1.263` |
| DeepSeek Harness（`@deepseek-ai/dsh`） | `0.1.2-rc.1` |
| pnpm | `11.7.0` |
| Tailscale | `1.102.3` |

已有 Gitea 部署沿用其原版本。完整部署与验证交接见 [HANDOFF.md](HANDOFF.md)。

构建镜像后的网关回归验证见 [tests/README.md](tests/README.md)。测试使用独立临时容器，不操作现有部署。
