# Gitea 普通私有仓库与容器内 Tailscale 部署交接

请在运行客户端容器的 Docker 主机上部署本仓库。Gitea、Claude Code、DeepSeek Harness 和 Tailscale 必须运行在**同一个 `git-server` 容器**内。宿主机不安装、不运行 Tailscale；不要修改宿主机已有的 Tailscale 配置，也不要改成 sidecar。

默认需求是把本机已有代码仓库直接 push 到 Gitea 的普通私有仓库，不需要 GitHub Token。保留已有 GitHub remote，另加 Gitea remote，分别推送。GitHub 拉取镜像仅在用户明确需要定期单向同步时启用。

## 部署与升级

1. 克隆完整仓库并运行 `./setup-gitea.sh`。可把已存在的客户端容器名作为参数，例如 `./setup-gitea.sh worker-1 worker-2`。不能只下载一个 sh 文件，脚本依赖 Dockerfile、锁文件和辅助文件。
2. 已有部署先更新仓库，再用原状态目录执行新版脚本，构建后重建容器以加入 Tailscale。脚本可从现有容器标签及本地 `.gitea-state-dir` 查找原目录；需要时显式传入原 `GITEA_DIR`。保留原 Gitea 镜像版本、管理员、仓库和数据卷，不删除旧数据。
3. 管理员用户名为 `gitadmin`，初始密码保存在状态目录的 `admin-password.txt`，已有密码不重置。不要把管理员凭据分发给所有客户端。
4. 在客户端自己的 Compose 文件中声明并加入 external network `git-net`，使其重建后仍能通过服务名访问。
5. 通过 `./manage.sh ...` 管理服务，使基础、harness 和 tailnet 配置全部加载。不要只用基础 `compose.yaml` 重建容器。

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
| Docker 内部容器 | `http://git-server:3000/OWNER/REPO.git`，或 `git@git-server:OWNER/REPO.git` |
| Docker 宿主机 | `http://127.0.0.1:3000/OWNER/REPO.git` |
| Tailnet 机器 | `https://GITEA.TAILNET.ts.net/OWNER/REPO.git` |

将域名替换为 `tailnet.sh` 返回的实际地址。内部容器只需加入 `git-net`，不需要加入 tailnet；远程机器使用容器节点的 HTTPS 入口。两类客户端访问同一个 Gitea 和同一套数据。

不能宣称只有 `443` 可从 tailnet 访问：userspace 网络栈还可能将发往该节点其他端口的连接转到容器回环地址，包括 Gitea `22`、`3000` 和以后手动启动的服务。按用户需求配置 tailnet ACL/grants；只需要 HTTPS 时只允许所需客户端访问此节点 `443`，并检查其他放行规则。接入 tailnet 不代表自动获得 Gitea 仓库权限。

## 创建普通私有仓库并推送本机代码

先查看目标仓库是否已存在。普通仓库目前没有专用创建脚本，需要调用 Gitea API 创建私有空仓库，关闭自动初始化；不能使用 `mirror-github.sh` 创建可写仓库。例如在 Docker 宿主机执行：

```bash
curl --fail-with-body --user gitadmin \
  --header 'Content-Type: application/json' \
  --data '{"name":"my-project","private":true,"auto_init":false}' \
  http://127.0.0.1:3000/api/v1/user/repos
```

把名称替换为实际项目名，按提示输入 Gitea 密码。目标已存在时保留它，核对类型、权限和内容；不要删除重建，不强制推送。

在用户已有工作副本中先运行 `git remote -v`，保留原有 remote，添加一个未占用的名称，例如：

```bash
git remote add gitea https://GITEA.TAILNET.ts.net/gitadmin/my-project.git
git push gitea HEAD
```

地址需按运行位置选择上表的可达入口。已有同名 remote 时先核对，不直接覆盖。这会推送当前分支；其他本地分支和标签按用户实际需求单独推送。原 GitHub remote（例如 `origin`）仍可独立使用，向 Gitea push 不会自动向 GitHub push。直接推送已有本机代码不需要 GitHub Token，Gitea 凭据或 SSH key 需单独配置。

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

先验证本机工作副本向普通 Gitea 仓库推送成功，再从一个 Docker 内部客户端和另一台真实 tailnet 机器验证 clone/pull，核对相同 ref 的提交 ID。可以使用用户已有提交，不需要制造测试提交。只检查网页或健康接口不能代替 Git 验证。

确认容器内存在受 s6 管理的 Tailscale 进程、节点已连接、Serve 配置指向 `127.0.0.1:3000`。重建后验证 Gitea 仓库、用户、工具工作副本以及 Tailscale 身份和 Serve 配置仍在。若无法访问另一台 tailnet 机器或未完成授权，明确记录尚未验证的范围，不声称已完成远程访问验证。

若项目使用 Git LFS，验证实际大文件下载。内部客户端优先使用 HTTP；SSH 的 LFS 认证可能返回基于 `ROOT_URL` 的地址，无法访问 tailnet 的内部客户端需配置可达的 LFS endpoint。子模块也需验证其各自 remote 的可达性。启用可选拉取镜像时，另外验证源仓库到镜像的同步结果。

完成后报告普通仓库地址、两类客户端入口、保留的 remote、状态目录、数据卷、接入容器、Tailscale 节点及 Git 验证结果；如启用了镜像，另报源仓库、同步间隔和最后成功同步状态。

保留 `local-git-server_git-data`、`local-git-server_agent-home`、`local-git-server_agent-workspace` 和 `local-git-server_tailscale-state`。不要删除卷或执行带 `-v` 的 Compose down。状态目录、密码、Token、节点密钥和备份不得提交到 Git。

参考：[Gitea API](https://docs.gitea.com/development/api-usage/)、[Gitea 仓库镜像](https://docs.gitea.com/usage/repository/repo-mirror/)、[Tailscale userspace 模式](https://tailscale.com/docs/concepts/userspace-networking)、[Tailscale Serve](https://tailscale.com/docs/reference/tailscale-cli/serve)。
