# Gitea Agent Server

在**同一个 `git-server` Docker 容器**中运行 Gitea、Claude Code、DeepSeek Harness（`dsh`）和 Tailscale。Gitea 提供私有 Git 仓库；两个代码工具通过独立的 `agent` 用户使用；Tailscale 在容器内运行，让其他 tailnet 机器访问 Git 服务。

默认用法是把本机已有仓库直接 push 到 Gitea，**不需要 GitHub Token**。原有 GitHub remote 可以保留，按需分别推送到两个服务器。GitHub → Gitea 的只读拉取镜像是可选功能。

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

这些容器会加入 `git-net`。项目级 SSH 授权使用下文的 `connect-client.sh`；客户端重建后的网络配置示例也在该节。

管理员为 `gitadmin`，初始密码保存在**脚本输出的状态目录**中的 `admin-password.txt`。已有管理员密码不会被重置。

## 把本机仓库 push 到 Gitea

先创建一个**普通的私有空仓库**，不要把它创建为拉取镜像。当前没有普通仓库创建辅助脚本，可在 Docker 宿主机终端调用 Gitea API；以下示例会提示输入 Gitea 密码：

```bash
curl --fail-with-body --user gitadmin \
  --header 'Content-Type: application/json' \
  --data '{"name":"my-project","private":true,"auto_init":false}' \
  http://127.0.0.1:3000/api/v1/user/repos
```

在已有本机工作副本中添加第二个 remote，然后推送当前分支：

```bash
git remote -v
git remote add gitea https://GITEA.TAILNET.ts.net/gitadmin/my-project.git
git push gitea HEAD
```

将 URL 替换为 `./tailnet.sh login` 输出的实际地址；在 Docker 宿主机上也可使用 `http://127.0.0.1:3000/gitadmin/my-project.git`。访问需要 Gitea 凭据。保留已有 `origin`；如果已有名为 `gitea` 的 remote，先核对地址，不直接覆盖。推送到 Gitea 不会自动更新 GitHub，之后仍可显式执行 `git push origin HEAD`。

## 让其他 Docker 容器免交互访问仓库

先创建上文所述的**普通可写仓库**。客户端容器需要已启动，安装 Git 和 OpenSSH，并已有本地 Git 工作副本；项目及其 Git 目录必须位于可写的 Docker volume 或 bind mount。宿主机还需要 Python 3。

在 **Docker 宿主机的本仓库目录**运行，第二个参数是客户端容器内的项目绝对路径，第三个参数不加 `.git`：

```bash
./connect-client.sh worker-1 /workspace/my-project gitadmin/my-project
```

脚本默认使用客户端容器配置的用户。若日常 Git 操作由 `agent` 用户执行，显式指定该用户；也可使用 `UID:GID`。数字 UID 在容器 `/etc/passwd` 中没有对应用户时，额外指定其 home：

```bash
CLIENT_USER=agent ./connect-client.sh worker-1 /workspace/my-project gitadmin/my-project

CLIENT_USER=1001:1001 CLIENT_HOME=/home/agent \
  ./connect-client.sh worker-1 /workspace/my-project gitadmin/my-project
```

脚本从原部署状态目录的 `admin-password.txt` 读取 `gitadmin` 凭据，也支持宿主机提供 `GITEA_USER` 和 `GITEA_PASSWORD`；该账号需有目标仓库的管理员权限以管理 deploy key。整个配置过程无需交互，管理员密码只用于授权，不会交给客户端。

它为该客户端和仓库配置可读写 deploy key，直接从 `git-server` 读取可信的 SSH 服务器公钥并写入专用 `known_hosts`，把客户端加入 `git-net`，设置 `gitea` remote，并验证读写连接。remote 使用生成的 SSH alias，实际连接 `git-server:22`；项目级 SSH wrapper 只对该 alias 使用专用配置，其他 SSH 目标沿用原有行为，`origin` 保持不变。脚本不会推送任何提交。

完成后，以配置时相同的用户在客户端项目目录运行，SSH 认证和服务器公钥确认均无需提示：

```bash
git fetch gitea
git push gitea HEAD
```

密钥和 SSH 配置保存在 Git common directory 下的 `.gitea-access`，普通仓库通常是 `.git/.gitea-access`。重建客户端时保留项目及 Git 目录所在的数据卷、原挂载路径和执行用户，即可保留访问配置；linked worktrees 共用 Git 配置和这份授权。同一目标的已有内部 HTTP `gitea` remote 可转换为 SSH；指向其他仓库的 remote、自定义 `core.sshCommand` 等冲突会使脚本停止，需先核对。

为让客户端重建后仍加入 `git-net`，把下面网络片段合并到客户端已有的 Compose 文件，将 `worker-1` 改为实际服务名：

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

此脚本用于其他 Docker 客户端容器。本机 M1 Max 上的 Git 工作副本仍按上节流程单独配置，尚未提供自动接入脚本。

## 访问地址

| 访问位置 | 地址 |
| --- | --- |
| `git-net` 中的客户端 | `http://git-server:3000/OWNER/REPO.git` |
| `git-net` 中的 SSH 客户端 | `git@git-server:OWNER/REPO.git`，端口 `22` |
| Docker 宿主机 | `http://127.0.0.1:3000` |
| Tailnet 机器 | `https://GITEA.TAILNET.ts.net/OWNER/REPO.git` |

`OWNER`、`REPO` 和域名均为占位符。普通私有仓库的 clone/pull/push 需要相应 Gitea 权限；SSH 需配置 key。默认网页链接使用 Docker 内部名称 `git-server`；配置 tailnet 后，网页链接统一使用实际 HTTPS 地址。

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

**Serve 并不把 tailnet 访问自动限制为 443。** Userspace 网络栈还可能把发往该节点其他端口的连接转到容器回环地址，包括 Gitea 的 `22`、`3000`，以及以后启动的其他服务。按实际需求配置 tailnet ACL/grants；如果只需要 HTTPS，应只允许所需客户端访问该节点的 `443`，并检查其他规则没有额外放行。Gitea 仓库权限仍然单独生效。

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

旧版本容器必须重新构建，才能加入容器内 Tailscale：

```bash
git pull --ff-only
./setup-gitea.sh
./tailnet.sh login
```

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
