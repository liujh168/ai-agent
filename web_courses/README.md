# 交互课件中心前端源码（web_courses/）

「🎓 交互课件中心」的 Vue 3 源码。构建产物经 `deploy-to-static.*` 脚本同步到
`ai_agent/static/courses/`，由 `ai_agent/web_ui.py` 在 launch 后挂载到 `/courses/` 提供访问。

## 开发

```powershell
npm install
npm run dev        # 本地开发，默认 http://localhost:5173
```

## 构建并部署到项目

```powershell
.\deploy-to-static.ps1
# 等价手动步骤：
#   npm run build
#   rm -rf ..\ai_agent\static\courses
#   cp -r dist ..\ai_agent\static\courses
```

部署后重启 Web UI（`python -m ai_agent.web_ui`）即可在「🎓 交互课件中心」页签看到最新课件。

## 结构

- `src/courses/`：四门课程（mcp / fc / rag / react），每门课一个目录
- `src/components/`：可复用组件（StepPlayer 步骤播放器 / CodeBlock 高亮代码块 / RealCode 真实源码对照区）
- `src/demos/`：零依赖 Python 教学脚本（同步发布到 `ai_agent/demos/`）
- `src/real/`：项目真实源码快照（经 Vite `?raw` 打包进「对照真实源码」折叠区；
  **项目源码大幅演进后需重新拷贝对应文件再构建**，否则对照区会与真实代码漂移）

## 在线访问

课件中心通过 GitHub Actions 自动构建并发布到 GitHub Pages：

- https://liujh168.github.io/ai-agent/
- https://code.liujh168.com/ai-agent/

`web_courses/` 下有变更并推送到 `master` 时自动重新部署。
