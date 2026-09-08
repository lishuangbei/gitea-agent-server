# Gitea GitHub 拉取镜像与双入口部署交接

请在运行客户端容器的 Docker 主机上部署附件 `setup-gitea.sh`。这个 Git 服务有两类客户端：同一 Docker 网络的容器，以及通过 Tailscale tailnet 接入的其他机器。两类客户端必须访问同一个 Gitea 实例和同一套仓库数据。

当前先采用临时方案：GitHub 是主仓库，Gitea 是定期更新的只读拉取镜像。两类客户端从 Gitea clone/pull；代码修改仍提交到 GitHub，随后由 Gitea 拉取同步。GitHub 的授权和 Gitea 客户端授权分别配置。部署脚本构建同一个容器中的 Gitea、Claude Code 和 DeepSeek Harness，并初始化服务，具体镜像需按以下步骤配置。已有 Gitea 要加入两个工具时，用原状态目录运行新版脚本，完成构建后重建容器并保留数据；已经包含工具时，添加拉取镜像无需重新部署。

## 访问约定

| 客户端 | Git 访问地址 |
| --- | --- |
| Docker 内部容器 | `http://git-server:3000/OWNER/REPO.git`，或 `git@git-server:OWNER/REPO.git` |
| Tailnet 机器 | `https://HOST.TAILNET.ts.net/OWNER/REPO.git` |

容器加入 external network `git-net`，不需要加入 tailnet。Tailnet 机器使用宿主机的 Tailscale Serve HTTPS 入口，不依赖 Docker 容器 IP，也不需要访问 Docker DNS 名称。`HOST.TAILNET.ts.net` 是占位符，必须替换为实际 Serve 地址。

## 部署步骤

1. 克隆完整仓库后运行脚本，把已存在的客户端容器名作为参数传入，例如 `./setup-gitea.sh worker-1 worker-2`。脚本依赖仓库内的 Dockerfile、锁文件和辅助脚本，不能只下载单个 sh 文件。管理员是 `gitadmin`，初始密码保存在状态目录的 `admin-password.txt`。
2. 在同一 Docker 主机上安装并登录 Tailscale；已经登录时沿用现有连接。先检查已有 Serve 配置，避免覆盖其他服务。如果默认 HTTPS 入口已被占用，先给 Git 服务安排独立的 Tailscale 节点或入口，不能直接替换现有服务。
3. 在可用的入口执行 `tailscale serve --bg 3000`，按提示启用 HTTPS，记录输出的实际 HTTPS 根地址。使用 Serve，保持访问范围为 tailnet；确认 tailnet 访问规则允许需要访问的机器。
4. 以相同状态目录再次运行脚本，例如：

   ```bash
   GITEA_DIR=/absolute/path/to/gitea-internal \
   GITEA_PUBLIC_URL=https://HOST.TAILNET.ts.net/ \
   bash setup-gitea.sh worker-1 worker-2
   ```

   `GITEA_DIR` 必须是第一次部署输出的状态目录。脚本把外部地址保存到 `compose.tailnet.yaml`，后续运行即使省略环境变量也会保留。用仓库中的 `./manage.sh ...` 操作 Compose，以便同时加载基础、harness 和 tailnet 配置。

5. 在客户端自己的 Compose 文件里声明并加入 external network `git-net`，确保客户端重建后仍能通过服务名连接。
6. 获取用户需要同步的 GitHub 仓库 URL。私有仓库用仅限目标仓库、Contents 只读权限的 GitHub fine-grained Token 授权；在目标环境安全填写 Token，不将其放入仓库 URL、代码或交付报告。组织仓库可能需要管理员批准。Gitea 容器需能出站连接 GitHub；无需开放公网入站。
7. 在 Gitea 网页通过「右上角 + → 迁移外部仓库 → GitHub」填写源仓库 URL、Access Token、Gitea 所有者和仓库名，明确勾选「私有」以及「此仓库为镜像 / This repository will be a mirror」。只迁移代码时不勾选 Issues、PR 等额外迁移项。不要先创建普通空仓库：拉取镜像应在迁移时建立。若目标名字已有普通仓库，保留原仓库，另选镜像名称。
8. 保留页面默认同步间隔并报告其实际值；首次迁移后在仓库设置中执行「立即同步 / Synchronize Now」，核对同步结果。镜像同步提交、分支、标签，是 GitHub → Gitea 单向同步；不配置向 GitHub 强制推送的 push mirror。
9. 为客户端配置 Gitea 仓库的读取权限。Docker 内部可用只读 SSH deploy key；tailnet 端默认使用 HTTPS 和 Gitea 凭据。避免把管理员凭据分发给所有客户端。保留已有仓库和历史，不把密码、Token 或私钥写入代码仓库。需要写代码的工作容器应另行配置 GitHub remote 和写入授权，不能向 Gitea 拉取镜像 push；不要改掉用户已有 remote。
10. 通过 `./agent-shell.sh` 以非 root 的 `agent` 用户进入同一个容器。Claude Code 和 dsh 在此用户下单独授权；完整 `/home/agent` 和 `/workspace` 使用独立命名卷持久化。工作副本放在 `/workspace`，不直接编辑 `/data` 中的服务端仓库。可先用 `./agent-shell.sh claude --version`、`./agent-shell.sh dsh --version` 检查安装。

## 验证与交付

从一台 Docker 内部客户端和另一台真实 tailnet 机器分别验证 clone、pull，并核对同一分支的提交 ID 一致。手动触发一次镜像同步，检查成功状态，并与 GitHub 源仓库的相同 ref 对比，确认同步方向和结果正确。可使用已有提交进行验证，不需要为了测试修改 GitHub 仓库。两类客户端访问的是只读镜像，不向 Gitea push；只检查网页或健康接口不能替代这些验证。

Gitea 的 ROOT_URL 和网页登录统一使用 tailnet HTTPS 地址；Docker 客户端的 remote 手动使用内部地址。脚本显式保持后端 HTTP，并设置 PUBLIC_URL_DETECTION=auto，让依赖请求地址的链接适配两个入口。Gitea 仓库权限仍然生效，接入 tailnet 不代表自动获得仓库权限。此配置未向 tailnet 开放 Git SSH。

若项目使用 Git LFS，优先让 Docker 客户端通过内部 HTTP 访问，确认已启用所需 LFS 镜像选项并验证实际文件下载。SSH 的 LFS 认证会返回以 ROOT_URL 为基础的 HTTP 地址，因此无法访问 tailnet 的内部 SSH 客户端需要另设可达的 LFS endpoint。子模块和自动化任务也应验证各自远端地址的可达性。

完成后报告 GitHub 源仓库、两类客户端的镜像 URL、同步间隔与最后成功同步状态、状态目录、持久化数据卷、接入容器和读取/同步验证结果。明确说明 GitHub 为主、Gitea 镜像只读。数据卷为 `local-git-server_git-data`；不要删除它或执行带 `-v` 的 Compose down。

参考：[Gitea 仓库镜像](https://docs.gitea.com/usage/repository/repo-mirror/)、[GitHub Token](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens)、[Gitea 反向代理](https://docs.gitea.com/administration/reverse-proxies/)、[Tailscale Serve](https://tailscale.com/docs/reference/tailscale-cli/serve)。
