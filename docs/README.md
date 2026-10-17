# docs/ 文档目录

本项目的文档按**用途**分为三个子目录，各目录独立维护、生命周期不同：

```
docs/
├── project/       ← 项目自身文档（设计决策、架构拆解、路线图）
├── knowledge/     ← 外部学习资料（同时作为 RAG 知识源）
└── blog/          ← 开发总结与体会（博客文章）
```

---

## 目录说明

### `project/` — 项目自身文档

项目专属，随代码迭代同步更新。包含设计决策、架构拆解、技术路线图等。新增项目文档请放到这里。

- [project/README.md](./project/README.md) — 本目录的文档清单与维护指南

### `knowledge/` — 外部学习资料

从外部获取的技术资料、行业方法论、论文笔记等。**同时作为 RAG 知识源**——CLI / Web UI 启动时会自动扫描本目录下的 `.md` / `.txt` / `.pdf` 文件进行向量化。

- [knowledge/README.md](./knowledge/README.md) — 来源标注与 RAG 配置说明

### `blog/` — 开发博客

项目开发过程中的总结、体会、踩坑笔记等。一篇文章一个 `.md` 文件，建议加 Hugo frontmatter（见下方模板）。

- [blog/README.md](./blog/README.md) — 写作规范与 frontmatter 模板
- 最新博文：[06-对照实验-上下文压缩与RAG检索](./blog/06-对照实验-上下文压缩与RAG检索.md) — 用可复现脚本（`experiments/`）给压缩与 FAISS 两条设计决策补上数据

---

## RAG 知识库配置

默认扫描目录：`docs/knowledge`。可通过环境变量覆盖：

```bash
# 扫描多个目录（分号分隔）
RAG_DIRECTORIES=docs/knowledge;data/papers
```

对应配置项：`Config.rag_directories`（默认 `("docs/knowledge",)`）。

---

## 文档维护约定

| 目录 | 新增文档 | 何时更新 | 格式要求 |
|------|---------|---------|---------|
| `project/` | 设计决策、技术拆解 | 代码变更后同步更新 | Markdown，代码位置精确引用 |
| `knowledge/` | 外部资料、行业报告 | 获取新资料时 | Markdown / PDF / TXT，文件名中文或英文均可 |
| `blog/` | 开发总结 | 阶段性产出 | Markdown + 可选 Hugo frontmatter |
