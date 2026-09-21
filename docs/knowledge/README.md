# knowledge/ — 外部学习资料（RAG 知识源）

本目录存放从外部获取的技术资料、行业方法论、论文笔记等。

**重要**：本目录是 Agent RAG 知识库的默认扫描源。CLI / Web UI 启动时会递归扫描本目录下的 `.md` / `.txt` / `.pdf` 文件，进行 embedding + 向量化，供 Agent 在对话中检索引用。

---

## 知识源清单

| 文件 | 来源 | 作者/机构 | 获取日期 |
|------|------|----------|---------|
| [大语言模型能力说明.md](./大语言模型能力说明.md) | 自撰 | — | 2026-09-14 |
| [工业流程企业与Agent应用.md](./工业流程企业与Agent应用.md) | 自撰 | 橘中新趣 | 2026-09-14 |
| [通用Agent时代价值迁移.md](./通用Agent时代价值迁移.md) | 自撰 | — | 2026-09-14 |

---

## 新增知识源时

1. **文件格式**：仅 `.md` / `.txt` / `.pdf` 三种后缀会被 RAG 扫描，其他格式请先转换
2. **文件命名**：中文标题或英文标题均可，保持简洁（不要超过 30 字符）
3. **来源标注**：在本 README.md 的"知识源清单"表格里追加一行，注明来源、作者、获取日期
4. **Frontmatter（可选）**：如果带 Hugo frontmatter，建议包含 `source_url` / `author` / `date` / `tags` 字段，便于后续整理
5. **缓存失效**：向量化结果缓存在项目根目录的 `_rag_cache.json` 里。新增/修改文件后，首次启动会自动检测 mtime 变化并重新向量化，无需手动清缓存

---

## RAG 配置

默认只扫描本目录。如需扩展多个目录，设置环境变量：

```bash
RAG_DIRECTORIES=docs/knowledge;data/papers;notes/reading
```

或在 `Config.from_env()` 之前修改 `rag_directories` 字段。

`rag_directories` 是相对于 `project_root` 的相对路径，程序启动时会自动拼接成绝对路径。
