# project/ — 项目自身文档

本目录存放与代码库直接绑定的文档，内容随项目迭代同步更新。

---

## 文档清单

| 文件 | 内容 | 更新时机 |
|------|------|---------|
| [设计决策清单.md](./设计决策清单.md) | 34 条设计决策，按架构/安全/RAG/Skill/集成/体验/评测 7 类组织，每条含代码片段、权衡分析、演进方向 | 新增/修改设计时同步更新 |
| [Skill实现本质原理.md](./Skill实现本质原理.md) | 以本项目为实例，拆解 Skill 的声明式定义、关键词路由、Prompt 替换、工具集收窄等核心机制 | Skill 机制变更时更新 |
| [深度理解agent模式选择.md](./深度理解agent模式选择.md) | FC/ReAct 协议对比、reasoning_content 折中、多 Agent 正交性、模型能力门槛、三模式对比实验与选择策略 | Agent 循环/协议实现变更时更新 |
| [RAG实现本质原理.md](./RAG实现本质原理.md) | 文本加载与多编码探测、滑块分块、批量 embedding、余弦检索、JSON 缓存与原子写入、RAG 作为普通工具被 FC 循环调用 | RAG 机制（rag.py / search_knowledge）变更时更新 |
| [上下文管理与历史压缩.md](./上下文管理与历史压缩.md) | 字符阈值触发、summarize_messages 五步算法、压缩 + DB 重写、边界对齐、无索引截断回退、CLI/Web 共用 | 历史压缩/截断逻辑变更时更新 |
| [MCP客户端协议拆解.md](./MCP客户端协议拆解.md) | Streamable HTTP + JSON-RPC 极简实现、initialize/tools/list/tools/call、mcp_ 前缀冲突解决、MCP 纳入统一安全策略、与 Skill 正交 | MCP 客户端/安全集成变更时更新 |
| [多Agent协作拆解.md](./多Agent协作拆解.md) | Planner/Executor/Critic 三角色独立 messages、Critic 审查重试循环、子任务上下文截断、两个调用入口（FC 工具 / Web 模式菜单） | multi_agent.py 协作流程变更时更新 |
| [ai-agent-learning-roadmap.html](./ai-agent-learning-roadmap.html) | 学习路线图（HTML 独立文件，保留原样） | 阶段性里程碑时更新 |

---

## 新增项目文档时

1. **先判断归属**：是项目技术拆解（放这里），还是外部知识源（放 `../knowledge/`），还是博客随笔（放 `../blog/`）
2. **引用真实代码**：每条设计决策/技术拆解必须标注源代码位置 + 关键代码片段，禁止伪代码
3. **同步更新清单**：在本 README.md 的"文档清单"表格里追加一行

---

## 与 RAG 的关系

本目录**不**作为 RAG 知识源扫描。理由：
- 项目自身文档是"给人读的"（设计决策、架构拆解），不是"喂给 LLM 检索的"
- 如果 Agent 自己的 RAG 索引了自己的设计文档，可能导致"循环知识"——RAG 检索到的内容是 Agent 本身的实现说明，用处有限
- 真正用于 RAG 的外部知识源统一放 `../knowledge/`

如需在设计文档里引用 RAG 示例，请在文字描述中说明，不要让设计文档本身进入 RAG 索引。
