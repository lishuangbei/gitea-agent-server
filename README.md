# Gitea Agent Server

在**同一个 Docker 容器**中运行 Gitea，并提供 Claude Code 和 DeepSeek Harness（`dsh`）命令行工具。Gitea 为其他容器提供 Git 服务；需要操作代码时，通过独立的 `agent` 用户进入该容器。

当前仓库同步方式：**GitHub 是主仓库，Gitea 保存定期更新的只读拉取镜像**。客户端从 Gitea clone/pull，代码修改提交到 GitHub。镜像配置、权限和双入口验证见 [HANDOFF.md](HANDOFF.md)。

## 快速开始

需要 Docker、Docker Compose v2、Bash，以及下载镜像和软件包的网络连接。

```bash
git clone https://github.com/lishuangbei/gitea-agent-server.git
cd gitea-agent-server
./setup-gitea.sh
```

脚本自动构建包含两个工具的镜像，启动 Gitea，并创建持久化数据卷。首次启动 Gitea 不需要 GitHub、Claude 或 DeepSeek 凭据。

如果已有其他容器需要访问 Git，可把容器名作为参数传入：

```bash
./setup-gitea.sh worker-1 worker-2
```

这些容器会加入 `git-net`。为保证客户端重建后仍能访问，还需在客户端自己的 Compose 配置中声明并加入 external network `git-net`。

管理员用户名为 `gitadmin`，初始密码保存在**脚本输出的状态目录**中的 `admin-password.txt`。已有管理员密码不会被重置。

## 访问地址

| 访问位置 | 地址 |
| --- | --- |
| 同一 Docker 网络中的客户端 | `http://git-server:3000/OWNER/REPO.git` |
| 同一 Docker 网络中的 SSH 客户端 | `git@git-server:OWNER/REPO.git`，端口 `22` |
| Docker 宿主机 | `http://127.0.0.1:3000` |
| 已配置 Tailscale 的远程机器 | `https://HOST.TAILNET.ts.net/OWNER/REPO.git` |

`OWNER`、`REPO` 和 `HOST.TAILNET.ts.net` 均为占位符。私有仓库需要单独配置 Gitea 读取权限或 SSH key。默认网页链接使用 Docker 内部名称 `git-server`；通过宿主机使用网页时，需要解析该名称，或按下文配置 tailnet HTTPS 地址。

## 使用 Claude Code 和 dsh

```bash
# 进入同一 Gitea 容器内的 Bash
./agent-shell.sh

# 直接启动 Claude Code
./agent-shell.sh claude

# 查看 DeepSeek Harness 的使用帮助
./agent-shell.sh dsh --help

# 查看 DeepSeek Harness 单次任务模式
./agent-shell.sh dsh --profile headless --help
```

这些命令以独立的 `agent` 用户（UID `1001`）运行，默认工作目录为 `/workspace`。Gitea 服务仍使用自己的 `git` 用户。

`/home/agent` 和 `/workspace` 分别保存在持久化卷中，容器重建后保留登录状态、工具设置和工作副本。首次使用工具时按提示完成各自的登录或授权；镜像和仓库不附带任何凭据。安装工具不包含服务额度，也不代表已验证付费 API 调用。

首次使用 dsh，可先执行 `./agent-shell.sh`，再在容器终端内输入：

```bash
read -rsp 'DeepSeek API key: ' DEEPSEEK_API_KEY
printf '\n'
export DEEPSEEK_API_KEY
dsh --profile headless "概述当前工作目录中的项目"
```

密钥输入不会回显，也不会作为命令文本进入 shell 历史。以上环境变量仅在当前 shell 有效；由 dsh 保存到 home 的设置和凭据才会随数据卷保留。宿主机已有的 API key 环境变量不会由 `agent-shell.sh` 自动传入。当前固定版本没有内置 `tui` profile；它的 Web 模式只允许监听容器回环地址，本仓库默认提供 headless 命令，不发布 dsh Web 端口。

请把工作副本克隆到 `/workspace`，通过 Git 操作仓库，不直接编辑 `/data` 中的 Gitea 服务数据。GitHub 写入授权、Gitea 读取授权和两个工具的授权分别配置。

## 通过 Tailscale 访问

在 **Docker 宿主机**安装并登录 Tailscale，先检查现有 Serve 配置，确认 HTTPS 入口可用：

```bash
tailscale serve status
tailscale serve --bg 3000
```

将输出的实际 HTTPS 根地址写入部署配置，同时沿用原状态目录：

```bash
GITEA_DIR=/absolute/path/to/gitea-internal \
GITEA_PUBLIC_URL=https://HOST.TAILNET.ts.net/ \
./setup-gitea.sh
```

把示例路径和域名替换为实际值。脚本保存外部地址，后续运行省略 `GITEA_PUBLIC_URL` 也会保留。已有 Serve 服务时应使用独立入口，避免替换其他服务。

Docker 客户端继续使用 `git-server:3000`，tailnet 机器通过 HTTPS 访问同一套仓库。Tailnet 访问规则需允许连接，Gitea 仓库权限仍然生效。此配置不向 tailnet 开放 Git SSH，也不发布 dsh Web 端口，上述 Serve 命令只转发 Gitea。

## 管理和更新

使用管理脚本操作 Compose，它会加载基础配置以及已生成的 harness、tailnet 配置：

```bash
./manage.sh ps
./manage.sh logs --tail=100
```

`compose.harness.yaml` 由部署脚本生成，用于构建镜像并添加工具所需的数据卷。不要只加载基础 `compose.yaml` 来重建服务，否则会遗漏这些配置。

更新仓库后再次运行部署脚本：

```bash
git pull
./setup-gitea.sh
```

脚本可通过已有容器的 Compose 标签找到原状态目录，并把路径保存到本地的 `.gitea-state-dir`，供容器停止或删除后继续使用；也可显式指定 `GITEA_DIR`。已有部署必须沿用原状态目录。构建完成后重建容器，保留已有配置、管理员和数据卷。

Gitea 数据卷沿用 `local-git-server_git-data`，包含仓库、数据库和服务配置。升级前备份状态目录及持久化数据；不要删除数据卷，也不要执行 `down -v`。新增两个工具的持久化卷同样需要备份。状态目录、密码文件、工具凭据和备份不应提交到 Git。

## 默认版本

| 组件 | 版本 |
| --- | --- |
| Gitea | `1.27.3` |
| Claude Code | `2.1.263` |
| DeepSeek Harness（`@deepseek-ai/dsh`） | `0.1.2-rc.1` |
| pnpm（用于 dsh 插件管理） | `11.7.0` |

已有 Gitea 部署沿用其原版本。完整部署和 GitHub 拉取镜像配置说明见 [HANDOFF.md](HANDOFF.md)。
