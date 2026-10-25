# GitHub Pages 部署与域名配置

> 本文记录本项目的 GitHub 仓库、Pages 站点与自定义域名的完整配置，供维护查阅。
> 最后更新：2026-10-25

## 一、全景总览

| 地址 | 内容 | 承载方式 |
|---|---|---|
| https://code.liujh168.com | 个人主页（潇湘棋士） | 仓库 `liujh168/liujh168.github.io`，分支 `main` 根目录 |
| https://code.liujh168.com/ai-agent/ | AI Agent 交互课件中心 | 仓库 `liujh168/ai-agent`，GitHub Actions 构建 `web_courses/` |
| https://liujh168.github.io | 301 → code.liujh168.com | 用户站绑定自定义域名后的默认行为 |
| https://liujh168.github.io/ai-agent/ | 301 → code.liujh168.com/ai-agent/ | 项目站继承用户站自定义域名 |
| https://www.liujh168.com | WordPress 博客「橘中新趣」 | **自托管服务器**（与 GitHub 无关） |

## 二、仓库清单

| 仓库 | 可见性 | 用途 |
|---|---|---|
| `liujh168/liujh168.github.io` | 公开 | 用户主页，单文件 `index.html` |
| `liujh168/ai-agent` | 公开（2026-10-25 由私有转公开，**Pages 要求公开仓库**） | 本项目；`web_courses/` 为课件中心源码 |

本地工作副本：`E:\liujh\AI-Agent`（ai-agent）、`E:\liujh\AI-Agent\liujh168.github.io`（主页，独立 git 仓库）。

## 三、域名与 DNS（阿里云云解析）

| 主机记录 | 类型 | 记录值 | 用途 |
|---|---|---|---|
| `code` | CNAME | `liujh168.github.io` | Pages 自定义域名 |
| `_github-pages-challenge-liujh168.code` | TXT | `6ddc853c3074360162ecb7db60268f` | GitHub 域名所有权验证 |
| `www` / `blog` / `jqmq` / `jdjk` / `jdjk-sms` | A | 服务器 IP | 自托管站点（与 Pages 无关，勿动） |

注意：**不要**在主页仓库配置 `www.liujh168.com` 为自定义域名——它由自托管服务器提供，配了会导致跳转混乱（发生过）。

## 四、GitHub 侧配置

### 4.1 主页仓库 `liujh168.github.io`

- Settings → Pages：
  - Source：**Deploy from a branch** → `main` / `(root)`
  - Custom domain：`code.liujh168.com`（DNS check 已通过）
  - **Enforce HTTPS**：已勾选
- 历史遗留：该仓库曾删除后重建，Pages 配置随仓库名恢复过旧值（master 分支 + www 域名），已修正

### 4.2 本项目仓库 `ai-agent`

- Settings → Pages：
  - Source：**GitHub Actions**
  - 工作流：`.github/workflows/pages.yml`（push 到 `master` 且 `web_courses/**` 有变更时触发，也可手动 Run workflow）
  - 流程：`npm ci` → `vite build` → 发布 `web_courses/dist`
- 另一个工作流：`.github/workflows/ci.yml`（Windows runner 上跑 Ruff + Pytest）
- ⚠️ 私有仓库**无法**使用 Pages（GitHub Enterprise 才支持），本项目已转为公开
- ⚠️ 仓库转为公开属于高风险操作，需 Passkey/验证器二次确认；操作前已做隐私排查（无 `.env`、无硬编码密钥、无个人敏感信息入库）

## 五、日常维护

### 更新主页

```bash
cd E:\liujh\AI-Agent\liujh168.github.io
# 修改 index.html 后
git add . && git commit -m "更新主页" && git push
```

push 后约 1 分钟生效。

### 更新课件中心

正常开发 `web_courses/` 后 push 到 `master` 即可自动构建部署（仅当 `web_courses/` 有变更时触发）。强制重新部署：Actions → Deploy to GitHub Pages → Run workflow。

## 六、故障排查手册

| 症状 | 原因 | 解法 |
|---|---|---|
| 访问 `*.github.io` 跳转到旧域名 | 浏览器缓存了 301（永久重定向浏览器会长期记忆） | 无痕窗口验证；或清除该站点缓存（地址栏锁图标 → 网站设置 → 清除数据） |
| 自定义域名 "DNS check is in progress" 卡住 | GitHub 边缘缓存了之前的负结果 | Pages 设置里 Remove 域名 → 重新填回 Save，重新触发校验 |
| HTTPS 证书迟迟不签发 | DNS check 未通过，证书排队中 | 等 DNS check 变绿后一般几分钟内签发；最长可达数小时 |
| `github.io/xxx/` 跳转到自定义域名后 404 | 项目站尚未启用 Pages 或尚未部署 | 确认仓库公开 + Pages Source 设置正确 + 工作流已成功运行 |
| 阿里云加了 TXT 验证记录但域名不生效 | 只完成了一半：还缺 CNAME 记录 | DNS 里补 CNAME（见第三节） |
| Pages 设置页提示 "Upgrade or make this repository public" | 私有仓库不能用 Pages | 仓库转公开（需 Passkey 验证；转之前做隐私排查） |

## 七、经验教训（踩过的坑）

1. **删除后重建的 `username.github.io` 仓库会恢复旧的 Pages 配置**（分支源、自定义域名），重建后务必检查 Settings → Pages。
2. **用户站绑定自定义域名后，所有项目站路径都会 301 到该域名下**（`liujh168.github.io/ai-agent/` → `code.liujh168.com/ai-agent/`），这是 GitHub 的正常行为，不是故障。
3. 阿里云解析添加 CNAME 时会先弹「解析变更确认」对话框，空记录值行会导致校验失败。
4. 浏览器自动化操作 React 表单时，`offsetParent` 对 `position:fixed` 元素为 null，判断可见性要用 `getBoundingClientRect()`。
