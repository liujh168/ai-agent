        raise ValueError("工具 schema 校验失败：\n- " + "\n- ".join(problems))


def make_summarize_history(client, get_history, set_history) -> callable:
    """
    生成一个 summarize_history 工具闭包。
    get_history / set_history 用于在 App 状态上读写 history（CLI / Web 各自的存储不同）。
    """

    def summarize_history() -> str:
        old = get_history()
        old_len = len(old)
        old_chars = sum(len(m.get("content") or "") for m in old)
        new = client.summarize_messages(old, keep_recent=4)
        set_history(new)
        new_len = len(new)
        new_chars = sum(len(m.get("content") or "") for m in new)
        return (
            f"✅ 对话历史摘要压缩完成！\n"
            f"  消息条数: {old_len} → {new_len}\n"
            f"  字符总数: {old_chars} → {new_chars}\n"
            f"  保留了最近 4 条完整消息，较早历史已浓缩为一段摘要"
        )

    return summarize_history


def compress_and_persist_history(client, history: list[dict], store, session_id: str | None) -> list[dict]:
    """摘要压缩 history，并把压缩结果重写进持久化存储（CLI / Web 共用）。返回压缩后的 history。"""
    compressed = client.summarize_messages(history, keep_recent=4)
    if session_id:
        store.clear_messages(session_id)
        # 重写当前 history（去掉 system）
        for message in compressed[1:]:
            store.append(
                session_id,
                message["role"],
                message.get("content", ""),
                message.get("tool_calls"),
            )
    return compressed


def ensure_test_image(path: str) -> bool:
    """生成图像识别演示用的测试图表（已存在则跳过）。返回 True 表示本次新建。"""
    if os.path.exists(path):
        log.debug("测试图片已存在，跳过生成: %s", path)
        return False
    try:
        from PIL import Image, ImageDraw

        img = Image.new("RGB", (640, 400), "white")
        draw = ImageDraw.Draw(img)
        data = [("AI Agent", 120), ("RAG", 200), ("Agentic", 160), ("Multi-Agent", 90)]
        x = 60
        for label, h in data:
            draw.rectangle([x, 400 - h, x + 120, 400], fill="steelblue")
            draw.text((x + 20, 400 - h - 25), label, fill="black")
            x += 140
        draw.text((20, 20), "2026 AI Field Attention Survey", fill="black")
        img.save(path)
        return True
    except (ImportError, OSError, ValueError) as exc:
        log.warning("测试图片生成失败: %s", exc)
        return False
