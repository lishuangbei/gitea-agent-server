# Gitea 可信网络 Git 入口与容器内 Tailscale 部署交接

请在运行客户端容器的 Docker 主机上部署本仓库。Gitea、Claude Code、DeepSeek Harness 和 Tailscale 必须运行在**同一个 `git-server` 容器**内。宿主机不安装、不运行 Tailscale；不要修改宿主机已有的 Tailscale 配置，也不要改成 sidecar。

默认需求是让自己的 Docker 容器和 tailnet 机器直接 clone/pull/push，不配置客户端密码、Token 或 SSH 密钥。已有代码第一次 push 到 `gitadmin/仓库名.git` 时自动创建普通私有仓库。保留已有 GitHub remote，另加 Gitea remote，分别推送，不需要 GitHub Token。GitHub 拉取镜像仅在用户明确需要定期单向同步时启用。

## 部署与升级

在 Docker host 的完整仓库目录运行，参数为需要接入的现有客户端容器名，可省略：

```bash
git pull --ff-only
./setup-gitea.sh worker-1 worker-2
```

首次部署先克隆 `https://github.com/lishuangbei/gitea-agent-server.git`，再执行 setup。不能只下载单个脚本，构建依赖 Dockerfile、锁文件和辅助文件。host 需要 Docker、Compose v2 和 Bash；默认 Git 客户端只需要 Git。

新版脚本构建镜像并重建服务，启用统一 Git 入口。已有部署沿用原状态目录，脚本从容器 Compose 标签及本地 `.gitea-state-dir` 查找；需要时显式传入原 `GITEA_DIR`。保留原 Gitea 镜像版本、管理员、仓库、工作副本和 Tailscale 状态，不删除卷。已经登录的 Tailscale 不需要重新授权。

管理员为 `gitadmin`，初始密码保存在状态目录的 `admin-password.txt`。脚本只在服务端使用保存的凭据配置统一身份，不分发给客户端，也不重置已有账号密码。如果用户改过密码且状态文件未更新，使用 `GITEA_PASSWORD` 环境变量提供当前密码。不要在报告、命令输出或仓库中泄露它。

setup 参数会把客户端加入 `git-net`。以后新加客户端也可单独执行 `docker network connect git-net CLIENT_CONTAINER`，无需重建 Gitea。把 external network 声明合并到客户端自己的 Compose 文件，并保留原有网络，使重建后仍能连接：

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

网络连接解决的是容器之间的通信。默认流程不运行 `connect-client.sh`，不创建客户端 deploy key，不要求项目 Git 目录持久化来保存认证；仍建议将工作副本放在数据卷中，保留用户代码。

通过 `./manage.sh ...` 管理服务，使基础、harness 和 tailnet 配置全部加载。不要只用基础 `compose.yaml` 重建容器。

## 连接容器内 Tailscale

在宿主机的克隆目录运行辅助脚本；实际 Tailscale 进程和命令均在容器内部执行：

```bash
./tailnet.sh login
```

首次默认节点名为 `gitea`。如需自定义，在首次登录时设置 `GITEA_TS_HOSTNAME=my-gitea`。脚本打印授权链接，最多等待 5 分钟；用户可在另一台设备打开链接完成授权，服务器无需 GUI。若超时，核对授权结果后重新执行。

已有 auth key 时使用 `./tailnet.sh login --auth-key`，在隐藏提示中输入。脚本通过 stdin 将 key 传给容器，不读取宿主机的 key 环境变量，不把 key 放进命令参数或仓库文件。已有 key 并不代表 tailnet 已启用 HTTPS；必要时仍需用户在其他设备打开提示链接并启用该功能。

登录成功后，脚本在容器内配置后台 Serve，将 HTTPS 转到同容器 `127.0.0.1:3000`，读取该节点实际 DNS 名称，写入 Gitea `ROOT_URL` 和 `compose.tailnet.yaml`，再重建容器使其生效。已经连接时可运行 `./tailnet.sh serve` 刷新入口，使用 `./tailnet.sh status` 查看节点和 Serve 状态。若容器内已有冲突的 Serve/Funnel 配置，脚本会停止；先检查现状，不直接清空或覆盖。

Tailscale 固定为 `1.102.3`，使用 userspace 模式，由现有 s6 监督进程管理；不需要 `NET_ADMIN` 或 `/dev/net/tun`。`tailscale-state` 卷挂载到 `/var/lib/tailscale`，目录 root 所有、权限 `0700`，保存节点身份、证书和后台 Serve 配置。保留此卷可在重启、重建后恢复同一节点及 Serve；不要把该身份复制到另一个同时运行的容器。

## 访问约定

| 客户端 | Git 地址 |
| --- | --- |
| 同一 Docker host、已加入 `git-net` 的容器 | `http://git-server:3000/gitadmin/REPO.git` |
| Docker 宿主机 | `http://127.0.0.1:3000/gitadmin/REPO.git` |
| Tailnet 机器 | `https://GITEA.TAILNET.ts.net/gitadmin/REPO.git` |

将域名替换为 `tailnet.sh` 返回的实际地址。内部容器不需要加入 tailnet，远程机器使用容器节点的 HTTPS 入口。两类客户端访问同一个 Gitea 和同一套数据，不需要额外 Git 凭据。不要使用可能变化的 Docker 内部 IP；`/workspace/splitx` 是磁盘工作副本，URL 中的 `gitadmin/splitx` 是 Gitea 账号和仓库名。

默认统一入口监听容器 `3000`，将 Git 和 Git LFS 请求以 `gitadmin` 身份送到回环地址的 Gitea 后端 `127.0.0.1:3001`；普通网页和 API 仍正常登录。不要把后端端口当成免认证入口，也不要让客户端保存管理员密码。

这是用户明确选择的可信网络访问方式：**任何能连接 HTTP/HTTPS Git 入口的客户端都可读写 `gitadmin` 有权访问的仓库**，服务端 Git 操作使用统一身份。仓库标记为“私有”或网页登录不会限制此入口的 Git 访问，tailnet 客户端也无需额外 Git 认证。host 端口保持仅绑定 `127.0.0.1:3000`，不新增公网映射；连接范围由 Docker 网络和现有 tailnet ACL/grants 决定。

不能宣称只有 `443` 可从 tailnet 访问：userspace 网络栈还可能把该节点其他端口转到容器回环地址，包括 `22`、`3000`、`3001` 和以后启动的服务。直接 SSH、普通 API 及 Gitea 后端仍执行各自的认证。按用户已有需求保留或配置 tailnet 访问规则，不把加入 tailnet 解释成仅放通 HTTPS。

## 推送已有代码并自动创建仓库

假设客户端容器已有 `/workspace/splitx` GitHub 工作副本，网络接通后，在客户端以平时操作 Git 的用户执行：

```bash
cd /workspace/splitx
git remote -v
git remote add gitea http://git-server:3000/gitadmin/splitx.git
git push gitea HEAD
```

目标不存在时首次 push 自动创建普通私有仓库，上传当前分支，无需事先调用 API、进入网页或输入密码。新建目标使用 `gitadmin` 命名空间。目标已存在时保留它，按正常 Git 规则推送；不要删除重建，不强制推送，也不要向只读拉取镜像或归档仓库推送。

首次推送包含 Git LFS 文件时，先通过管理员 API 创建普通私有空仓库，再进行免凭据 Git/LFS 推送；LFS pre-push 上传先于 Git receive-pack，目标尚不存在时无法自动创建。普通 Git 的首次 push 自动建库不受影响。

已有同名 `gitea` remote 时先核对目标，再使用 `git remote set-url gitea URL` 更新为对应 HTTP/HTTPS 地址。保留原 GitHub `origin`；向 Gitea push 不会自动向 GitHub push。其他分支和标签按用户需求单独上传。

其他已加入 `git-net` 的容器直接运行：

```bash
git clone http://git-server:3000/gitadmin/splitx.git
```

M1 Max 等 tailnet 客户端用同样的 Git 操作，改用上表中的实际 HTTPS 地址。无需为这些机器设置 deploy key。容器中的任意工作副本都不会自动发布到 Gitea，只有 push 后服务端才保存为托管仓库。

## 可选：旧版 SSH 接入脚本

`connect-client.sh` 保留为可选方式，不再是推荐接入步骤。只有明确选择 SSH 时才使用：

```bash
./connect-client.sh worker-1 /workspace/my-project gitadmin/my-project
CLIENT_USER=agent ./connect-client.sh worker-1 /workspace/my-project gitadmin/my-project
```

SSH 方式要求目标仓库已存在，host 有 Python 3，客户端有 Git/OpenSSH，Git common directory 位于可写持久化卷。脚本从服务端状态取得管理员凭据，为单个仓库注册可写 deploy key，配置可信服务器公钥、专用 SSH alias 和 `gitea` remote；不会推送提交。`origin` 保持不变。数字 UID 没有 `/etc/passwd` 条目时还需 `CLIENT_HOME`。

密钥与配置保存在 Git common directory 的 `.gitea-access` 中，linked worktrees 共用。重建客户端时保留卷、挂载路径和 Git 执行用户。SSH 设置不会关闭默认 HTTP/HTTPS 统一入口，也不会收窄它的访问范围。

## 容器内代码工具

通过 `./agent-shell.sh` 以独立的非 root `agent` 用户（UID `1001`）进入同一容器。可运行 `./agent-shell.sh claude`、`./agent-shell.sh dsh --help`。工作副本放在 `/workspace`，不直接编辑 `/data` 中的服务端仓库。

`/home/agent` 和 `/workspace` 使用独立命名卷持久化。Claude Code 和 dsh 分别授权，镜像不附带凭据。dsh 当前使用 headless profile，API key 在容器 shell 内隐藏输入的示例见 README。宿主机 API key 环境变量不会由 `agent-shell.sh` 自动传入。不要声称已验证付费模型请求；没有提供 dsh Web 启动或端口映射辅助脚本，手动启动 Web 服务时也要检查 tailnet 可达范围。

## 可选：GitHub → Gitea 只读镜像

仅在需要定期从 GitHub 单向拉取时执行：

```bash
./mirror-github.sh https://github.com/OWNER/REPO.git optional-mirror-name
```

源私有仓库需要 GitHub 授权。脚本可复用宿主机 `GH_TOKEN`、`GITHUB_TOKEN` 或已登录的 `gh`，否则隐藏提示输入 Token。仅镜像代码时可限定目标仓库并授予 Contents 读取权限；组织仓库可能需要批准。Gitea 保存凭据以便后续同步，不应把它写进仓库 URL 或报告。

该脚本创建私有拉取镜像，只同步 Git 提交、分支和标签，不导入 Issues、PR 或 Git LFS 文件。不要预先创建普通空仓库；同名仓库存在时脚本停止且不覆盖。拉取镜像只读，不能向其 push。

需要手动同步时运行 `./mirror-github.sh --sync GITEA_OWNER/MIRROR_NAME`。返回只代表任务排队，之后必须检查同步状态和源仓库相同 ref 的提交 ID。不得配置向 GitHub 强制推送的 push mirror。

## 验证与交付

验证一个尚不存在的 `gitadmin` 目标由首次 push 自动创建，仓库默认私有，再从另一个 Docker 内部客户端和另一台真实 tailnet 机器验证 clone/pull，核对相同 ref 的提交 ID。Git 客户端不应持有 Gitea 密码、Token 或专用密钥，整个过程没有认证提示；原 `origin` 保持原配置。验证更新已有仓库仍能 push，网页及普通 API 仍要求正常认证。可使用用户已有提交，不需要制造测试提交。只检查网页或健康接口不能代替 Git 验证。

确认容器内存在受 s6 管理的 Tailscale 进程、节点已连接、Serve 配置指向 `127.0.0.1:3000`。重建后验证 Gitea 仓库、用户、工具工作副本以及 Tailscale 身份和 Serve 配置仍在。若无法访问另一台 tailnet 机器或未完成授权，明确记录尚未验证的范围，不声称已完成远程访问验证。

若项目使用 Git LFS，验证实际大文件上传和下载，以及返回的对象地址可由该类客户端访问。子模块需验证其各自 remote 的可达性；旧 GitHub URL 仍遵循 GitHub 的认证规则。可选 SSH 流程需另外验证客户端重建后的 fetch/push 和 SSH LFS endpoint。启用可选拉取镜像时，另外验证源仓库到镜像的同步结果。

完成后报告普通仓库地址、两类客户端入口、保留的 remote、状态目录、数据卷、接入容器、Tailscale 节点及 Git 验证结果；如启用了镜像，另报源仓库、同步间隔和最后成功同步状态。

保留 `local-git-server_git-data`、`local-git-server_agent-home`、`local-git-server_agent-workspace` 和 `local-git-server_tailscale-state`。不要删除卷或执行带 `-v` 的 Compose down。状态目录、密码、Token、节点密钥和备份不得提交到 Git。

参考：[Gitea API](https://docs.gitea.com/development/api-usage/)、[Gitea 仓库镜像](https://docs.gitea.com/usage/repository/repo-mirror/)、[Tailscale userspace 模式](https://tailscale.com/docs/concepts/userspace-networking)、[Tailscale Serve](https://tailscale.com/docs/reference/tailscale-cli/serve)。
