import asyncio
from functools import partial

import httpx
import pytest

from taichu.application.vector_graph.models import (
    VectorGraphEvidence,
    VectorGraphSourceType,
)
from taichu.infrastructure.vector_graph.reranker import BGEReranker, _reranker_text


def test_reranker_text_exposes_graph_relations_and_bounds_content() -> None:
    evidence = VectorGraphEvidence(
        passage_id="passage-1",
        source_type=VectorGraphSourceType.KNOWLEDGE_CARD,
        source_id="item-1",
        source_ref="knowledge:item-1",
        title="一叶金莲",
        content="甲" * 5_000,
        content_sha256="0" * 64,
        rank=1,
        relation_texts=["一叶金莲 有助突破至 法相境"],
    )

    text = _reranker_text(evidence)

    assert "标题：一叶金莲" in text
    assert "图关系：一叶金莲 有助突破至 法相境" in text
    assert text.endswith("甲" * 4_000)
    assert "甲" * 4_001 not in text


def test_vector_graph_evidence_exposes_bounded_reranker_score() -> None:
    evidence = VectorGraphEvidence(
        passage_id="passage-1",
        source_type=VectorGraphSourceType.KNOWLEDGE_CARD,
        source_id="item-1",
        source_ref="knowledge:item-1",
        title="一叶金莲",
        content="正文",
        content_sha256="0" * 64,
        rank=1,
        reranker_score=0.95,
    )

    assert evidence.reranker_score == 0.95


def _evidence(index: int = 0) -> VectorGraphEvidence:
    return VectorGraphEvidence(
        passage_id=f"passage-{index}", source_type=VectorGraphSourceType.KNOWLEDGE_CARD,
        source_id=f"item-{index}", source_ref=f"knowledge:item-{index}",
        title="测试", content=str(index), content_sha256="0" * 64, rank=index + 1,
    )


def test_parallel_reranks_queue_only_inference_requests(monkeypatch) -> None:
    active = 0
    peak = 0

    async def respond(request: httpx.Request) -> httpx.Response:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return httpx.Response(200, json=[{"index": 0, "score": 0.9}])

    monkeypatch.setattr(httpx, "AsyncClient", partial(httpx.AsyncClient, transport=httpx.MockTransport(respond)))

    async def scenario() -> None:
        reranker = BGEReranker(base_url="http://rerank", model_id="test", timeout_seconds=1)
        results = await asyncio.gather(*[
            reranker.rerank(str(i), [_evidence(i)], top_k=1) for i in range(3)
        ])
        assert [result[0].source_id for result in results] == ["item-0", "item-1", "item-2"]

    asyncio.run(scenario())
    assert peak == 1


@pytest.mark.parametrize("status,expected_calls", [(429, 3), (400, 1)])
def test_reranker_retries_only_temporary_overload_with_a_bound(monkeypatch, status, expected_calls) -> None:
    calls = 0

    async def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status, headers={"Retry-After": "0"}, json={"error": "测试错误"})

    monkeypatch.setattr(httpx, "AsyncClient", partial(httpx.AsyncClient, transport=httpx.MockTransport(respond)))
    reranker = BGEReranker(base_url="http://rerank", model_id="test", timeout_seconds=2)
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(reranker.rerank("测试", [_evidence()], top_k=1))
    assert calls == expected_calls


def test_reranker_recovers_from_overload_without_losing_batch_scores(monkeypatch) -> None:
    import json
    calls = 0

    async def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        texts = json.loads(request.content)["texts"]
        return httpx.Response(200, json=[
            {"index": index, "score": int(text.rsplit("：", 1)[1]) / 100}
            for index, text in enumerate(texts)
        ])

    monkeypatch.setattr(httpx, "AsyncClient", partial(httpx.AsyncClient, transport=httpx.MockTransport(respond)))
    reranker = BGEReranker(base_url="http://rerank", model_id="test", timeout_seconds=2)
    results = asyncio.run(reranker.rerank("测试", [_evidence(i) for i in range(65)], top_k=2))
    assert calls == 3
    assert [item.source_id for item in results] == ["item-64", "item-63"]


def test_reranker_queue_wait_is_bounded_by_request_timeout(monkeypatch) -> None:
    calls = 0

    async def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=[{"index": 0, "score": 0.8}])

    monkeypatch.setattr(httpx, "AsyncClient", partial(httpx.AsyncClient, transport=httpx.MockTransport(respond)))
    reranker = BGEReranker(base_url="http://rerank", model_id="test", timeout_seconds=0.02)
    async def scenario() -> None:
        async with reranker._inference_lock:
            with pytest.raises(TimeoutError):
                await reranker.rerank("测试", [_evidence()], top_k=1)
        assert calls == 0
        # 被取消的排队者不会泄漏许可或阻止后续请求。
        assert len(await reranker.rerank("测试", [_evidence()], top_k=1)) == 1

    asyncio.run(scenario())
    assert calls == 1
