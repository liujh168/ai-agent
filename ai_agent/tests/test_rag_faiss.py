# ============================================================
# RAG FAISS 可选加速（mock embedding，不依赖 LM Studio）
# ============================================================

import os

import pytest

from ai_agent import rag as rag_module
from ai_agent.rag import RAGKnowledgeBase

_DIM = 4
_CHUNK_VECTORS = [
    [float((i * j + i + j) % 7 + 1) for j in range(_DIM)] for i in range(16)
]
_QUERY_VECTOR = [3.0, 1.0, 2.0, 5.0]


def _write_kb_file(tmp_path, words: int = 40) -> str:
    md = os.path.join(tmp_path, "kb.md")
    with open(md, "w", encoding="utf-8") as f:
        f.write(" ".join(f"w{i:03d}" for i in range(words)))
    return md


def _install_fake_embed(monkeypatch):
    calls = {"n": 0}

    def fake_embed(self, texts):
        calls["n"] += len(texts)
        if len(texts) > 1:
            return [_CHUNK_VECTORS[i] for i in range(len(texts))]
        return [_QUERY_VECTOR]

    monkeypatch.setattr(RAGKnowledgeBase, "_embed", fake_embed)
    return calls


def _build_kb(tmp_path, monkeypatch, faiss_threshold: int = 2000) -> RAGKnowledgeBase:
    _install_fake_embed(monkeypatch)
    md = _write_kb_file(tmp_path)
    kb = RAGKnowledgeBase(
        chunk_size=20,
        chunk_overlap=0,
        cache_file=os.path.join(tmp_path, "_rag_cache.json"),
        faiss_threshold=faiss_threshold,
    )
    assert kb.build([md]) == 10
    return kb


class TestNumpyFallback:
    def test_below_threshold_uses_numpy(self, tmp_path, monkeypatch):
        kb = _build_kb(tmp_path, monkeypatch)
        assert kb._faiss_index is None
        results = kb.search_detailed("查询", top_k=3)
        assert len(results) == 3
        expected = sorted(
            ((kb._cosine(_QUERY_VECTOR, _CHUNK_VECTORS[i]), i) for i in range(10)),
            reverse=True,
        )[:3]
        assert [r.chunk_index for r in results] == [i for _, i in expected]
        for result, (score, _) in zip(results, expected):
            assert result.score == pytest.approx(score)

    def test_threshold_boundary_uses_numpy(self, tmp_path, monkeypatch):
        kb = _build_kb(tmp_path, monkeypatch, faiss_threshold=10)
        assert kb._faiss_index is None

    def test_search_tuple_api_unchanged(self, tmp_path, monkeypatch):
        kb = _build_kb(tmp_path, monkeypatch)
        results = kb.search("查询", top_k=2)
        assert len(results) == 2
        assert all(isinstance(item, tuple) and len(item) == 2 for item in results)


class TestFaissUnavailable:
    def test_missing_faiss_falls_back(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rag_module, "faiss", None)
        monkeypatch.setattr(rag_module, "np", None)
        kb = _build_kb(tmp_path, monkeypatch, faiss_threshold=0)
        assert kb._faiss_index is None
        results = kb.search_detailed("查询", top_k=3)
        assert len(results) == 3
        assert results[0].score >= results[-1].score


class TestFaissPath:
    def test_faiss_index_built_over_threshold(self, tmp_path, monkeypatch):
        pytest.importorskip("faiss")
        kb = _build_kb(tmp_path, monkeypatch, faiss_threshold=3)
        assert kb._faiss_index is not None

    def test_faiss_results_match_numpy(self, tmp_path, monkeypatch):
        pytest.importorskip("faiss")
        kb = _build_kb(tmp_path, monkeypatch, faiss_threshold=3)
        faiss_results = kb.search_detailed("查询", top_k=4, min_score=0.5)
        kb._faiss_index = None
        numpy_results = kb.search_detailed("查询", top_k=4, min_score=0.5)
        assert [r.chunk_index for r in faiss_results] == [r.chunk_index for r in numpy_results]
        assert [r.text for r in faiss_results] == [r.text for r in numpy_results]
        assert [r.source for r in faiss_results] == [r.source for r in numpy_results]
        for f_result, n_result in zip(faiss_results, numpy_results):
            assert f_result.score == pytest.approx(n_result.score, abs=1e-5)

    def test_faiss_index_rebuilt_from_cache(self, tmp_path, monkeypatch):
        pytest.importorskip("faiss")
        md = _write_kb_file(tmp_path)
        cache_file = os.path.join(tmp_path, "_rag_cache.json")
        calls = _install_fake_embed(monkeypatch)
        kb = RAGKnowledgeBase(
            chunk_size=20, chunk_overlap=0, cache_file=cache_file, faiss_threshold=3
        )
        assert kb.build([md]) == 10
        assert calls["n"] == 10
        kb2 = RAGKnowledgeBase(
            chunk_size=20, chunk_overlap=0, cache_file=cache_file, faiss_threshold=3
        )
        assert kb2.build([md]) == 10
        assert calls["n"] == 10
        assert kb2._faiss_index is not None
        assert kb2.vectors == kb.vectors
        faiss_results = kb2.search_detailed("查询", top_k=4)
        kb2._faiss_index = None
        numpy_results = kb2.search_detailed("查询", top_k=4)
        assert [r.chunk_index for r in faiss_results] == [r.chunk_index for r in numpy_results]
