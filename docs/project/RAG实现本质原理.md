# RAG 实现本质原理

> 以本项目（AI Agent 交互式演示，基于 LM Studio 本地大模型）为实例，拆解 RAG（Retrieval-Augmented Generation，检索增强生成）的核心实现机制。
> 所有代码引用均可按 `文件:行号` 定位核实。

---

## 一、先搞清楚：RAG 到底是什么

LLM 是无状态的纯函数：它只知道训练时的知识，看不到你硬盘上的任何文件。想让模型"基于我的资料回答"，只有两条路：

| 维度 | 把全文塞进 Prompt | RAG（本项目做法） |
|------|------------------|------------------|
| 上下文成本 | 全文几万字一次塞入，token 爆炸 | 只塞最相关的 5 个片段（top_k=5） |
| 可扩展性 | 上下文窗口是硬上限，文档多了装不下 | 检索在窗外做，知识库可以无限大 |
| 幻觉控制 | 资料没塞进去的部分，模型照样编 | 检索不到就明说"资料中未找到" |
| 实现依赖 | 零依赖 | 需要一个 embedding 模型把文本变向量 |

一句话概括 RAG 的实现本质：**把"找资料"这一步从模型脑子里搬到模型窗外——先用向量相似度检索出相关片段，再把片段作为参考资料塞进 Prompt 让模型回答**。

在这个项目里，RAG 还有第二个身份：**它只是 22 个原子工具中的一个**（`search_knowledge`）。LLM 不知道自己在"做 RAG"，它只是在 Function Calling 循环里调了一个叫 `search_knowledge` 的工具，拿到一段文本结果，仅此而已。

---

## 二、架构全景：RAG 在整个系统里的位置

```
┌──────────────────────────────────────────────────────────────────────────┐
│  启动阶段（一次性）  main.py:121-149  _build_knowledge_base()              │
│                                                                          │
│  collect_rag_files(project_root, ["docs/knowledge", "data"])             │
│    → 递归扫描 .md / .txt / .pdf（rag.py:299-310）                         │
│    → RAGKnowledgeBase.build(rag_paths)（rag.py:99-153）                   │
│       ┌─ 命中缓存？ ──Yes──→ 直接加载 JSON，跳过全部计算                     │
│       └─ No → 抽文本 → 分块 → 批量 embedding → 原子写入缓存                 │
└──────────────────────────────┬───────────────────────────────────────────┘
                               │  内存中持有：chunks / vectors / sources
                               ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  注册阶段  main.py:157-166  _register_tools()                             │
│    build_tool_map(client, lambda: self.rag, ...)                          │
│      → 生成闭包 search_knowledge(question)（app_core.py:585-589）          │
│      → 与其他 21 个工具一起进入 tool_map                                   │
└──────────────────────────────┬───────────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  运行阶段（每轮对话）  runtime.py:90-183  AgentRuntime.run()               │
│                                                                          │
│  用户: "ReAct 和 Function Calling 有什么区别？"                            │
│    → LLM 看到 search_knowledge 的 schema（app_core.py:329-340）           │
│    → LLM 返回 finish_reason="tool_calls"，调 search_knowledge             │
│    → ToolExecutor 执行闭包 → rag.answer(question, client)                  │
│         ├─ query 向量化（一次 embedding 调用）                              │
│         ├─ 手写余弦相似度对全部 chunk 打分 → 取 top_k                      │
│         └─ 把片段拼成 Prompt → 再调一次 chat 模型 → 返回带引用的答案         │
│    → 结果作为 role="tool" 消息喂回 history → 下一轮循环                    │
│    → LLM 基于工具结果输出最终回答                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

**关键洞察**：RAG 在本项目里有两个完全分离的生命周期——**构建期**（启动时一次性分块+向量化，产物是内存里的向量表和磁盘上的 JSON 缓存）和**查询期**（对话中每次调用 `search_knowledge`，只做一次 query 向量化 + 一次相似度扫描）。构建慢、查询快，中间的桥梁就是版本化缓存。

---

## 三、核心模块拆解（一）：文本加载与分块

`RAGKnowledgeBase` 全部实现都在 `ai_agent/rag.py`（310 行），核心状态是四个等长列表（`rag.py:56-59`）：

```python
self.chunks: list[str] = []           # 文本片段
self.vectors: list[list[float]] = []  # 每个片段的向量
self.sources: list[str] = []          # 每个片段来自哪个文件（绝对路径）
self.chunk_indices: list[int] = []    # 每个片段在原文件中的分块序号
```

这四个列表按下标对齐，就是本项目"向量数据库"的全部——没有 Chroma、没有 FAISS，就是四个 Python list。

### 3.1 多编码自动探测（rag.py:71-91）

```python
def _extract_text(self, path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        reader = PdfReader(path)
        parts = []
        for page_no, page in enumerate(reader.pages, 1):
            try:
                parts.append(page.extract_text() or "")
            except (ValueError, KeyError, TypeError, AttributeError, OSError) as exc:
                log.warning("PDF %s 第 %d 页文本抽取失败，已跳过: %s", path, page_no, exc)
                continue
        return "\n".join(parts)
    if ext in (".md", ".txt"):
        for enc in ("utf-8", "utf-8-sig", "gbk", "latin-1"):
            try:
                with open(path, encoding=enc) as f:
                    return f.read()
            except UnicodeDecodeError:
                continue
        raise RuntimeError(f"无法识别文件 {path} 的编码")
    raise RuntimeError(f"不支持的文件类型：{ext}（仅支持 {self.SUPPORTED_EXT}）")
```

两个细节值得注意：

1. **PDF 逐页容错**：单页抽取失败只记 warning 跳过该页（`rag.py:77-81`），不让一页坏掉拖垮整个文件。
2. **编码探测是顺序试错**：依次尝试 `utf-8 → utf-8-sig → gbk → latin-1`。`latin-1` 是兜底——它能解码任何字节序列，所以只要文件存在就不会"完全打不开"，最多内容乱码。中文教学资料常遇到 GBK 编码的 txt，这个顺序让 utf-8（最常见）零成本命中，GBK 一次重试命中。对应设计决策清单 **R4**。

### 3.2 分块（chunking）策略（rag.py:133-142）

```python
for path in valid_paths:
    text = re.sub(r"\s+", " ", self._extract_text(path))
    step = self.chunk_size - self.chunk_overlap
    source = str(Path(path).resolve())
    for index, start in enumerate(range(0, max(1, len(text) - self.chunk_overlap), step)):
        chunk = text[start : start + self.chunk_size]
        if chunk.strip():
            self.chunks.append(chunk)
            self.sources.append(source)
            self.chunk_indices.append(index)
```

这是最朴素的**固定窗口滑块分块**：先 `re.sub(r"\s+", " ", ...)` 把全文压成单行，再以 `chunk_size=500` 字符为窗口、`chunk_overlap=50` 字符为重叠（即步长 450）滑动切片。

**为什么要重叠 50 字符？** 如果一个关键句子正好横跨两个 chunk 边界（"……ReAct 的本质是 | 思考-行动-观察……"），没有重叠时两个 chunk 各自只有半句话，检索两边都拿不准。50 字符重叠保证边界附近的句子至少完整地出现在一个 chunk 里。

参数合法性在 `build()` 开头校验（`rag.py:114-117`）：`chunk_size` 必须大于 0，`chunk_overlap` 必须在 `[0, chunk_size)` 内，否则 `step` 会变成 0 或负数导致死循环。

**为什么不用语义分块（按段落/句子切）？** 教学项目选最简单的方案：固定窗口人人能看懂，效果对教学足够；而且"它切得很粗糙"本身就是一个好教学点——chunk 边界切断句子的问题肉眼可见，能引出对语义分块的讨论。配置入口在 `config.py:71-72`（`chunk_size` / `chunk_overlap`），可被 `RAG_CHUNK_SIZE` / `RAG_CHUNK_OVERLAP` 环境变量覆盖（`config.py:111-112`）。

---

## 四、核心模块拆解（二）：向量化与检索

### 4.1 批量 embedding 调用（rag.py:62-69, 144-150）

```python
def _embed(self, texts: list[str]) -> list[list[float]]:
    resp = requests.post(
        f"{self.base_url}/embeddings",
        json={"model": self.embed_model, "input": texts},
        timeout=self.request_timeout,
    )
    resp.raise_for_status()
    return [item["embedding"] for item in resp.json()["data"]]
```

embedding 用的是 LM Studio 本地加载的 `nomic-embed-text-v1.5`（默认模型名见 `config.py:70`），走 OpenAI 兼容的 `/embeddings` 接口——**`input` 直接传一个字符串列表，一次 HTTP 请求返回一批向量**。

构建索引时按每批 16 个 chunk 提交（`rag.py:144-150`）：

```python
batch_size = 16
for start in range(0, len(self.chunks), batch_size):
    self.vectors.extend(self._embed(self.chunks[start : start + batch_size]))
if len(self.vectors) != len(self.chunks):
    raise RuntimeError(
        f"embedding 返回数量不一致：chunks={len(self.chunks)}, vectors={len(self.vectors)}"
    )
```

为什么批量？每个 chunk 单独调用，N 个 chunk 就是 N 次 HTTP 往返，本地推理引擎每次还要重新调度。批量化把固定开销摊薄到 1/N，16 是"单次请求不太大、批数不太多"的经验值。批后立刻校验数量一致性——embedding 服务如果悄悄丢了结果，在这里就炸掉，而不是等到检索时下标越界。

### 4.2 手写余弦相似度（rag.py:93-97）

```python
def _cosine(self, a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0
```

三行数学：点积除以两个模长的乘积。不引 numpy——向量只有几百维、chunk 只有几百个，纯 Python 扫一遍是毫秒级；**依赖少一项，教学项目就能少装一个包**。零向量（全 0 embedding 的异常情况）返回 0.0 而不是除零崩溃。对应设计决策清单 **R3**。

### 4.3 检索：一次 embedding + 全表扫描（rag.py:159-176）

```python
def search_detailed(
    self,
    query: str,
    top_k: int = 5,
    min_score: float = 0.0,
) -> list[RetrievedChunk]:
    if not query.strip() or not self.chunks or not self.vectors:
        return []
    query_vector = self._embed([query])[0]
    if self._faiss_index is not None:            # FAISS 可选加速分支，见第八章
        return self._search_faiss(query_vector, top_k, min_score)
    scored = [(self._cosine(query_vector, vector), i) for i, vector in enumerate(self.vectors)]
    scored.sort(reverse=True)
    return [
        self._to_chunk(score, index)
        for score, index in scored[:top_k]
        if score >= min_score
    ]
```

这就是"向量数据库查询"的全部：**query 向量化一次 → 与内存中每个向量算余弦 → 排序 → 取前 top_k 且分数不低于 min_score 的**（`self._faiss_index is not None` 时改走 FAISS 内积索引，语义完全等价，见第八章「已实现扩展：FAISS 可选加速」）。返回的 `RetrievedChunk`（`rag.py:26-31`，frozen dataclass）带着来源文件和分块序号，让答案能标注出处。另有一个 `search()`（`rag.py:155-157`）只做元组转换，是向后兼容的旧 API。

### 4.4 answer()：检索结果如何变成 Prompt（rag.py:219-244）

```python
def answer(self, question: str, client, top_k: int = 5, max_tokens: int = 1024, min_score: float = 0.2) -> str:
    results = self.search_detailed(question, top_k, min_score=min_score)
    if not results:
        return "资料中未找到相关内容。"
    context = "\n\n".join(
        f"[片段{i + 1} | 来源: {Path(item.source).name} | 分块: {item.chunk_index}]\n{item.text}"
        for i, item in enumerate(results)
    )
    messages = [
        {
            "role": "system",
            "content": (
                "你是知识库问答助手，只能依据提供的资料片段回答。"
                "资料中没有的信息要明确说明'资料中未找到'。"
            ),
        },
        {"role": "user", "content": f"参考资料：\n{context}\n\n问题：{question}"},
    ]
    return client.chat(messages, max_tokens=max_tokens)
```

`answer()` 是 RAG 的"G"（Generation）环节，三个设计点：

1. **检索兜底**：`min_score=0.2` 过滤低相关片段，一个都不剩时直接返回固定话术，**根本不调 LLM**——不给模型编造的机会；
2. **来源标注**：每个片段带 `[片段i | 来源: 文件名 | 分块: 序号]` 头，模型回答时可以引用出处，人也方便回溯验证；
3. **人格收窄**：system prompt 明确"只能依据提供的资料片段回答"，把通用助手临时收窄成知识库问答器——这和 Skill 用场景化 prompt 换人格是同一个手法。

注意 `answer()` 内部用的是独立的 `messages` 列表——它是一段**隔离的子对话**，不污染主对话历史；返回的答案字符串作为工具结果回到主循环。

---

## 五、核心模块拆解（三）：JSON 缓存、版本化 key 与原子写入

构建索引要对全部 chunk 调 embedding，首次可能要几分钟。如果每次启动都重来，项目没法用。缓存在 `rag.py:246-296`。

### 5.1 版本化 cache key（rag.py:246-257）

```python
def _cache_key(self, valid_paths: list[str]) -> str:
    parts = [
        str(self.CACHE_VERSION),
        self.embed_model,
        str(self.chunk_size),
        str(self.chunk_overlap),
    ]
    for path in valid_paths:
        resolved = Path(path).resolve()
        stat = resolved.stat()
        parts.append(f"{resolved}:{stat.st_size}:{stat.st_mtime_ns}")
    return "|".join(parts)
```

cache key 把**所有影响索引内容的因素**都编码进去：缓存格式版本（`CACHE_VERSION = 2`，`rag.py:37`）、embedding 模型、分块参数，以及每个文件的绝对路径 + 大小 + 纳秒级修改时间。**任何一个变了，key 就变了，缓存自动失效重建**——改了一个 md 文件、换了 embedding 模型、调了 chunk_size，都无需手动删缓存文件。这就是"版本化"的含义：缓存自带失效逻辑，不存在"脏缓存"。

### 5.2 缓存校验（rag.py:259-280）

```python
def _load_cache(self):
    if not os.path.exists(self.cache_file):
        return None
    try:
        with open(self.cache_file, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None

def _valid_cache(self, cached, key: str) -> bool:
    if not isinstance(cached, dict) or cached.get("version") != self.CACHE_VERSION:
        return False
    if cached.get("key") != key:
        return False
    chunks = cached.get("chunks")
    ...
    return length > 0 and len(vectors) == length and len(sources) == length and len(indices) == length
```

两道防线：读文件时 JSON 损坏（比如上次写入被中断）静默返回 `None` 走全量重建；读进来后**既要 key 匹配、又要四个列表等长且非空**才认这个缓存。任何校验失败的结果都是"重新构建"，而不是报错——缓存永远是优化项，不是正确性依赖。对应设计决策清单 **R1**。

### 5.3 原子写入（rag.py:282-296）

```python
def _write_cache(self, key: str) -> None:
    cache_path = Path(self.cache_file)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": self.CACHE_VERSION,
        "key": key,
        "chunks": self.chunks,
        "vectors": self.vectors,
        "sources": self.sources,
        "chunk_indices": self.chunk_indices,
    }
    temp_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(temp_path, cache_path)
```

**先写完整内容到 `.tmp` 文件，再 `os.replace()` 改名覆盖**。`os.replace` 在 Windows 和 POSIX 上都是原子操作：读者要么看到旧文件、要么看到新文件，永远看不到"写了一半的 JSON"。配合 5.2 的校验，即使程序在写缓存中途被 Ctrl+C 杀掉，下次启动也只是重建索引，不会带着半个缓存文件跑。对应设计决策清单 **R2**。

### 5.4 配置入口（config.py）

RAG 相关的配置项集中在 `Config` dataclass 里：

| 字段（config.py 行号） | 环境变量 | 作用 |
|---|---|---|
| `embed_model`（:70） | `EMBED_MODEL`（:110） | embedding 模型名 |
| `chunk_size` / `chunk_overlap`（:71-72） | `RAG_CHUNK_SIZE` / `RAG_CHUNK_OVERLAP`（:111-112） | 分块窗口与重叠 |
| `rag_cache_file`（:73） | `RAG_CACHE_FILE`（:113） | 缓存文件路径（默认项目根 `_rag_cache.json`） |
| `rag_directories`（:89） | `RAG_DIRECTORIES`（:125-129，分号分隔） | 扫描目录，默认 `("docs/knowledge",)` |

注意分块参数和 embedding 模型同时也是 cache key 的一部分——**配置即缓存指纹**，改配置等于自动换缓存，两条机制是闭环的。

---

## 六、RAG 作为普通工具：如何被 Function Calling 循环调用

这是本项目 RAG 最值得讲的一层：**RAG 对 Agent 循环完全透明**。

### 6.1 注册：一个闭包（app_core.py:585-589, 635）

```python
def search_knowledge(question: str) -> str:
    rag = get_rag()
    if rag is None:
        return "知识库不可用（初始化失败），请检查 PDF 与 embedding 模型。"
    return rag.answer(question, client)
```

`search_knowledge` 不是模块级函数，而是 `_build_demo_tools()` 里生成的**闭包**：捕获 `get_rag`（无参可调用，返回 `RAGKnowledgeBase` 实例或 `None`）和 `client`，然后在 `app_core.py:635` 以 `"search_knowledge": search_knowledge` 进入 tool_map。

**为什么用 `lambda: self.rag` 延迟取值，而不是直接传实例？** 关键在 Web UI 端：CLI 的启动顺序是先 `_build_knowledge_base()`（main.py:240）再 `_register_tools()`（main.py:242），直接传实例也行；但 Web UI 的 RAG 是在首个会话到来时才延迟初始化的（`web_ui.py:255-280` 的 `init_rag()`，配 `threading.Lock` 双重检查），而工具表在服务启动时就已构建（`web_ui.py:198` 传入的正是 `lambda: self.rag`）。闭包在**调用时**取 `get_rag()`，所以"注册时 RAG 还没建好"和"调用时 RAG 不可用（初始化失败返回友好提示而不是抛异常）"两种情况都被同一个机制覆盖。

### 6.2 对 LLM 的呈现：一份普通 schema（app_core.py:329-340）

```python
{
    "type": "function",
    "function": {
        "name": "search_knowledge",
        "description": "RAG 知识库问答：基于项目内 PDF/MD/TXT 资料检索并回答。当用户询问 AI Agent 概念、原理、资料内容时调用",
        "parameters": {
            "type": "object",
            "properties": {"question": {"type": "string", "description": "需要查询的问题"}},
            "required": ["question"],
        },
    },
},
```

LLM 看到的只是"一个叫 search_knowledge 的工具，收一个 question 字符串"。它不知道背后是向量检索，也不需要知道——**封装边界就是工具边界**。路由靠 `BASE_SYSTEM_PROMPT` 里的一句话（`app_core.py:54`）："用户询问【AI Agent 概念/资料内容/知识库/查一下资料】时，调用 search_knowledge（本地 PDF 知识库）"，与 `web_search` 的"互联网搜索"在 schema description（`app_core.py:136`）里明确互斥。

### 6.3 执行：统一循环里的普通一环（runtime.py:121-167）

`AgentRuntime.run()` 的 Function Calling 循环对 `search_knowledge` 没有任何特殊处理。命中工具调用的那一轮：

```python
if finish_reason == "tool_calls":
    history.append(message.model_dump())
    for tool_call in message.tool_calls or []:
        name = tool_call.function.name            # "search_knowledge"
        raw_args = tool_call.function.arguments   # '{"question": "..."}'
        ...
        result = executor.execute(name, arguments)  # → 闭包 → rag.answer()
        history.append(
            {
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result.content,         # 带引用的答案文本
            }
        )
        ...
    continue   # 回到循环顶部，带着工具结果再问一次 LLM
```

循环-执行-回灌的每一步都和 `calculator`、`get_weather` 走同一条代码路径（`runtime.py:90-183` 的完整骨架，另见 `docs/project/深度理解agent模式选择.md` §一）。**RAG 的检索+生成两次 LLM 调用全部发生在 `executor.execute()` 这一次同步调用内部**——对外它只是一个"比较慢的工具"。这也是 Skill 能把 `search_knowledge` 写进 `required_tools`（`skills/example_plan.md`）、ReAct 演示能保留它（`app_core.py:487-490` 特意注释"它调用 RAG，不递归"）的原因：工具就是工具，谁都能编排。

---

## 七、完整流程走一遍

假设首次启动后，用户问：**"根据资料，ReAct 模式和普通 Function Calling 有什么区别？"**

```
Step 1 — 启动，构建索引（main.py:237-247）
  AgentDemoApp.run()
    → _build_knowledge_base()（main.py:121）
        → Config.from_env() 读 RAG_* 环境变量
        → collect_rag_files(project_root, ["docs/knowledge", "data"])（rag.py:299）
            → 扫到 docs/knowledge/ 下 3 个 .md 文件
        → rag.build(paths)（rag.py:99）
            → _cache_key() 计算指纹；_load_cache() 未命中（首次）
            → 3 个文件抽文本 → 空白压缩 → 滑块分块（500/50）→ 得到 N 个 chunks
            → 按批 16 个调 /embeddings → 得到 N 个向量
            → _write_cache()：写 _rag_cache.json.tmp → os.replace 原子改名
        → 打印 "[OK] 知识库就绪：3 个文件 → N 个文本片段"

Step 2 — 注册工具（main.py:157-166）
  build_tool_map(client, lambda: self.rag, ...)
    → 闭包 search_knowledge 捕获 get_rag 与 client，进入 tool_map

Step 3 — 用户提问，进入 Function Calling 循环（runtime.py:90）
  history = [system prompt, user: "根据资料，ReAct 模式和普通 Function Calling 有什么区别？"]
  请求 1：LLM 看到 22 个工具 schema
    → BASE_SYSTEM_PROMPT 第 54 行指引生效
    → 返回 finish_reason="tool_calls"，调 search_knowledge({"question": "ReAct 和 Function Calling 的区别"})

Step 4 — 工具执行（runtime.py:143 → app_core.py:585 → rag.py:219）
  executor.execute("search_knowledge", {"question": "..."})
    → get_rag() 拿到实例（非 None）
    → rag.answer(question, client)
        → _embed([query])：一次 HTTP，query 变成向量
        → _cosine 对全部 N 个向量打分、排序
        → top 5 且 score ≥ 0.2 的片段入选（比如命中 大语言模型能力说明.md 的两段、
          通用Agent时代价值迁移.md 的三段）
        → 拼成 "参考资料：\n[片段1 | 来源: ...]\n...\n\n问题：..."
        → client.chat()：知识库子对话，LLM 只依据片段作答
    → 返回带引用的答案字符串

Step 5 — 结果回灌，继续循环（runtime.py:145-167）
  history.append({"role": "tool", "tool_call_id": ..., "content": 答案字符串})
  请求 2：LLM 看到工具结果
    → finish_reason="stop"（或 None），输出最终回答
    → runtime.py:169-179 yield final 事件，循环结束

Step 6 — 用户看到回答
  "根据资料：ReAct 是边想边做……（片段来自 大语言模型能力说明.md）……"

Step 7 — 第二次启动（缓存命中）
  rag.build() → _cache_key() 指纹不变 → _valid_cache() 通过
    → 直接从 _rag_cache.json 加载 chunks/vectors，全程零 embedding 调用
    → 打印就绪信息几乎是瞬间
```

---

## 八、边界与扩展

### 本项目的 RAG 不做什么（保持最小版本）

| 特性 | 状态 | 原因 |
|------|------|------|
| 向量数据库（FAISS/Chroma） | ✅ FAISS 可选加速已实现 | 默认仍是全表余弦扫描；chunk 数超过 `faiss_threshold`（默认 2000）且安装了 `faiss-cpu` 时自动切换 `IndexFlatIP`，见下文「已实现扩展」 |
| 语义分块（按段落/句子） | ❌ 固定窗口 | 切边界的缺陷本身就是教学素材；参数可调（`RAG_CHUNK_SIZE`） |
| 混合检索（BM25 + 向量） | ❌ 仅向量 | 关键词稀疏检索对代码/专有名词更准，是明确的下一步扩展点 |
| 重排序（rerank） | ❌ 无 | top_k=5 直取， rerank 模型会再引入一个服务 |
| 增量索引（单文件更新） | ❌ 全量重建 | key 一变全重建；教学规模下重建成本可接受 |
| 多轮改写查询（HyDE 等） | ❌ 无 | query 原样向量化；多轮对话中指代消解靠主循环 LLM 重写 question 参数 |

### 如果想扩展

1. **换真向量数据库**（✅ 已实现，见下文）：`_cosine` 全表扫描（`rag.py:170`）之上已有 FAISS `IndexFlatIP` 可选加速。接口只动了 `build()` 和 `search_detailed()` 两个点，工具层无感——这正是"RAG 封装为普通工具"的红利；
2. **加语义分块**：在 `rag.py:133-142` 的滑块循环前先做段落切分（按 `\n\n`，注意 `rag.py:134` 已把空白压平，需调整顺序），再在段落上做窗口合并；
3. **加混合检索**：`search_detailed()` 里并行算一份 BM25 分数，与余弦分数加权融合；
4. **调 min_score 观察幻觉**：把 `rag.py:225` 的 `min_score=0.2` 改成 0 跑一遍——低相关片段混进 Prompt 后答案质量的变化，是最直观的 RAG 调参实验；
5. **评测驱动调参**：`eval.py:581` 有 `--no-rag` 开关、`test_core.py:286` 的 `TestRAGOffline` 用 mock embedding 离线测试构建/检索/缓存，改分块或检索策略后可直接跑 `pytest ai_agent/tests/test_core.py -k RAG` 验证不回归。

### 进阶练习：把"❌"变成"动手实现"

上面表格里标 ❌ 的四项（语义分块、混合检索、rerank、增量索引）恰好是工业界 RAG 的四块标准拼图，而本项目的最小实现给每一项都留好了接缝。每个练习给出了源码锚点和可检查的验收标准，彼此独立，按任意顺序做。

#### 练习 1：语义分块——按段落切分再窗口合并

> 原文方向："在 `rag.py:133-142` 的滑块循环前先做段落切分（按 `\n\n`），再在段落上做窗口合并。"

- **背景**：当前分块是固定窗口滑块（`rag.py:133-142`）：先把全文 `re.sub(r"\s+", " ", ...)` 压成单行（`:134`），再以 500 字符窗口、50 字符重叠切片。句子被从中间切断是肉眼可见的缺陷——而且段落结构在 `:134` 的空白压平这一步就已经被销毁了。
- **任务**：调整顺序实现语义分块：先按 `\n\n` 切成段落（可在空白压平之前做，或按原始文本切段、段内再压空白），再把短段落**贪心合并**进 `chunk_size` 窗口——装不下就开新窗口；单段超过 `chunk_size` 的才退回原滑块逻辑切。`chunk_overlap` 在段落模式下可退化为"相邻窗口共享最后一个段落"。
- **提示**：改动集中在 `build()` 的 `:133-142`；不可破坏的硬约束是四个列表等长对齐（`rag.py:56-59`：chunks / vectors / sources / chunk_indices），分块逻辑怎么变，每产出一个 chunk 就要同步 append 另外三个列表。cache key 已含 `chunk_size`/`chunk_overlap`（`:246-257`），改分块策略时记得 bump `CACHE_VERSION`（`:37`），否则旧缓存会被误命中。
- **验收标准**：仿 `TestRAGOffline`（`test_core.py:387-408`）用 monkeypatch 的假 embedding 离线构建：准备一份含"关键句恰好横跨 500 字符边界"的 md，断言固定窗口策略检索不到完整句、语义分块策略能命中；`pytest ai_agent/tests/test_core.py -k RAG` 通过。

#### 练习 2：BM25 + 向量混合检索

> 原文方向："`search_detailed()` 里并行算一份 BM25 分数，与余弦分数加权融合。"

- **背景**：纯向量检索对语义相似强、对精确词弱——专有名词、函数名、错误码这类"字面必须对上"的查询，embedding 常常给出反直觉的低分。BM25（关键词稀疏检索）正好互补。
- **任务**：`search_detailed()`（`rag.py:159-176`）里并行计算一份 BM25 分数，与余弦分数各自归一化后加权融合（如 `0.7 * cos + 0.3 * bm25`）再排序取 top_k；融合权重做成构造参数（如 `hybrid_alpha`）方便对比实验。
- **提示**：**不要引 rank_bm25 依赖**——BM25 的核心（IDF + TF 饱和曲线）约 20 行纯 Python，中文分词按字或二元组（bigram）切就够教学用，这与"手写余弦相似度而不引 numpy"（设计决策 R3）是同一个取舍。两套分数量纲不同，必须先各自 min-max 归一化再加权，否则融合权重没有实际意义。FAISS 分支（`:170-171`）和纯 Python 分支拿到的候选集不同，混合检索建议统一在"两路各取 top_k*2 候选 → 融合重排"上做。
- **验收标准**：离线构造一个"关键词完全匹配但 mock 向量分低"的 chunk（让它余弦分排第 3 之外），断言纯向量检索漏掉它、混合检索（alpha > 0）命中；沿用 `test_core.py:388` 的 monkeypatch embedding 手法，不依赖 LM Studio。

#### 练习 3：rerank 重排序——先召回 15 条，再让 LLM 精选 5 条

> 原文方向："top_k=5 直取，rerank 模型会再引入一个服务。"

- **背景**：检索是"一次 embedding + 全表扫描取 top_k=5"（`rag.py:159-176`），向量分数的排序直接决定哪些片段进 Prompt。向量分数粗排够用但不精细——工业界的标准做法是**两阶段检索**：第一阶段粗召回更多候选，第二阶段用更强的模型精排。
- **任务**：实现 LLM rerank：先向量召回 `top_k * 3`（15 条候选），再让 chat 模型对候选与 query 的相关性打分/排序，取前 5 进入 `answer()` 的 Prompt；rerank 多花的这一次 LLM 调用做成可选开关。
- **提示**：rerank 的 LLM 调用照 `answer()`（`rag.py:219-244`）的隔离子对话模式写：独立 messages、低 temperature、要求只输出 JSON 序号列表（如 `{"ranking": [3, 1, 7, ...]}`）。JSON 解析的鲁棒兜底直接复用 `_parse_critic_decision` 的三级思路（`multi_agent.py:104-150`）。最重要的一条工程原则：**rerank 失败（超时/乱码/序号越界）必须静默回退为向量粗排序**——参照摘要失败回退硬截断（`client.py:256-295`）的"优化项不是正确性依赖"原则。
- **验收标准**：mock client 让 rerank 返回打乱顺序的序号列表，断言最终片段顺序按 rerank 结果重排；再让 rerank 调用抛异常，断言静默回退向量序、检索结果不丢；两种路径都进 `TestRAGOffline` 所在的离线测试文件。

#### 练习 4：增量索引——单文件变化只重向量化该文件

> 原文方向："key 一变全重建；教学规模下重建成本可接受。"

- **背景**：`_cache_key()`（`rag.py:246-257`）把**所有文件**的 path+size+mtime 编码进一个整体 key——改一个 md 文件的标点，整个知识库（可能几百个 chunk）全部重新 embedding。文件越多，这个"全量重建"越疼。
- **任务**：把缓存粒度从"整个知识库一个 key"细化到"每个文件一个 key"：缓存结构改为按文件组织（如 `{"files": {path: {key, chunks, vectors, chunk_indices}}}`），重建时逐文件对比 stat，未变文件的 chunks/vectors 直接从缓存复用，只有变化的文件重新抽文本 + embedding。
- **提示**：`CACHE_VERSION` 必须 bump（`rag.py:37`）——旧版整体缓存会被 `_valid_cache`（`:259-280`）的 version 检查自动拒绝，平滑过渡到新格式。原子写入 `_write_cache`（`:282-296`）原样复用，不需要第二套写盘逻辑。FAISS 索引由合并后的全量向量确定性重建（`:186-217`），不用动。注意保持四个全局列表（`rag.py:56-59`）按下标对齐——复用旧 chunk 时 `chunk_indices` 的序号语义要想清楚是"文件内序号"还是"全局序号"。
- **验收标准**：mock `_embed` 计数调用次数：构建 2 个文件 → 修改其中 1 个 → 重新 `build()`，断言第二次 embedding 只处理了变化文件的 chunk 数；再断言全文件不变时第二次构建零 embedding 调用；`pytest ai_agent/tests/test_core.py -k RAG` 全绿。

### 已实现扩展：FAISS 可选加速（rag.py:186-217）

`faiss-cpu` 是**可选依赖**（`pip install ai-agent-learning[faiss]`，见 `pyproject.toml` 的 `[project.optional-dependencies]`），不装也不影响任何功能。行为规则只有一条：**faiss 可用且 chunk 数超过 `faiss_threshold`（构造参数，默认 2000）时，`build()` 末尾用 L2 归一化后的向量构建 `faiss.IndexFlatIP` 内积索引，检索走 FAISS；否则静默走原来的纯 Python 全表扫描**。阈值可通过配置项 `Config.rag_faiss_threshold`（环境变量 `RAG_FAISS_THRESHOLD`）调整，CLI/Web/评测入口都会透传。

```python
def _build_faiss_index(self) -> None:
    self._faiss_index = None
    if faiss is None or len(self.vectors) <= self.faiss_threshold:
        return
    matrix = np.array(self.vectors, dtype="float32")
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    matrix = np.divide(matrix, norms, out=np.zeros_like(matrix), where=norms > 0)
    index = faiss.IndexFlatIP(matrix.shape[1])
    index.add(matrix)
    self._faiss_index = index
```

三个设计点：

1. **语义等价**：`IndexFlatIP` 算的是内积，向量先 L2 归一化后内积就是余弦相似度——与 `_cosine` 的结果一致（仅 float32/float64 精度差），`search_detailed()` 的返回结构、排序与 `min_score` 过滤在两条路径下完全相同；
2. **优雅回退**：模块顶部 `try: import faiss` 失败时 `faiss = None`，任何路径都不会抛 ImportError——不装 `faiss-cpu` 时项目行为与之前逐字节一致；
3. **索引不进缓存**：`_rag_cache.json` 只缓存 chunks/vectors，FAISS 索引由向量确定性重建、没有缓存价值——缓存命中后同样调 `_build_faiss_index()` 重建（毫秒级），向量缓存的失效逻辑一行未动。

---

## 九、总结

RAG 的实现本质只有三句话：

1. **检索就是把文本变成数字再比距离**：分块 → embedding → 余弦相似度排序，全部算法加起来不到 30 行，没有向量数据库也能成立；
2. **生成就是把检索结果变成 Prompt 的一部分**：`answer()` 的全部工作是拼一段带出处标注的 `参考资料：` 塞进 user 消息，配一个收窄人格的 system prompt，然后普通地调一次 chat；
3. **RAG 对 Agent 透明**：它只是一个闭包 + 一份 schema 注册进 tool_map，被 Function Calling 循环当普通工具调用——**工程上的难点从来不在 RAG 本身，而在缓存指纹、原子写入、延迟初始化这些让它"每次都能正常工作"的细节上**。

理解了这三点，再看 `rag.py` 会发现它短得惊人（310 行，其中三分之一是缓存）。**RAG 不是一个框架，而是"embedding 调用 + 相似度排序 + Prompt 拼装"三件已知事情的顺序组合**——剩下的全是工程卫生。
