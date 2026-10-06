#!/usr/bin/env python3
"""rag_score_probe — RAG 유사도 점수 분포를 뽑아 데이터 기반으로 threshold를 산출한다.

에이전트와 동일하게 질의를 임베딩(Ollama /api/embeddings, prompt=text)한 뒤
Qdrant를 threshold 없이 검색해, 각 히트의 실제 score를 출력한다. '정답' 매치의 점수를
보고, 그보다 약간 낮게 RAG Score Threshold를 잡으면 된다.

의존성: 없음(파이썬 표준 라이브러리만). 로봇/외부 호스트 어디서든 실행.

사용법:
  # 기본 질의셋(충전대/거실/키친)으로 실행
  python3 rag_score_probe.py \
    --ollama-url http://<ollama-host>:11434 \
    --embed-model qwen3-embedding:8b \
    --qdrant-url http://10.159.172.74:6333 \
    --collection robo_claw_former_w2_2f_2

  # 사이트별 질의셋을 파일로 (JSON: [{"query": "...", "expect": "..."}, ...])
  python3 rag_score_probe.py ... --queries-file myqueries.json

주의: --ollama-url / --embed-model 은 에이전트가 쓰는 값(RC_LLM_EMBEDDING_BASE_URL,
RC_LLM_EMBEDDING_MODEL)과 동일해야 점수가 일치한다.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

DEFAULT_QUERIES = [
    {"query": "충전대 위치를 알려줘", "expect": "충전대"},
    {"query": "충전대 위치 좌표를 알려줘", "expect": "충전대"},
    {"query": "거실의 위치를 알려줘", "expect": "거실"},
    {"query": "키친 위치를 알려줘", "expect": "키친"},
    {"query": "충전대로 이동해", "expect": "충전대"},
    {"query": "거실로 이동해", "expect": "거실"},
]


def _post_json(url: str, payload: dict, timeout: float = 30.0) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def embed(ollama_url: str, model: str, text: str) -> list[float]:
    """에이전트와 동일: POST /api/embeddings {model, prompt}."""
    url = ollama_url.rstrip("/") + "/api/embeddings"
    try:
        out = _post_json(url, {"model": model, "prompt": text})
        emb = out.get("embedding")
        if emb:
            return emb
    except urllib.error.URLError:
        pass
    # 신형 Ollama 폴백: /api/embed {model, input} -> {embeddings: [[...]]}
    url2 = ollama_url.rstrip("/") + "/api/embed"
    out = _post_json(url2, {"model": model, "input": text})
    embs = out.get("embeddings") or []
    if embs:
        return embs[0]
    raise RuntimeError("임베딩 응답에 벡터가 없습니다. --ollama-url/--embed-model 확인")


def qdrant_search(qdrant_url: str, collection: str, vector: list[float], limit: int) -> list[dict]:
    """threshold 없이 상위 limit개 검색 → [{score, text, type, name}]."""
    url = qdrant_url.rstrip("/") + f"/collections/{collection}/points/search"
    out = _post_json(url, {"vector": vector, "limit": limit, "with_payload": True})
    hits = []
    for p in out.get("result", []):
        payload = p.get("payload", {}) or {}
        meta = payload.get("metadata", {}) or {}
        hits.append(
            {
                "score": float(p.get("score", 0.0)),
                "text": str(payload.get("text", ""))[:70],
                "type": meta.get("type", ""),
                "name": meta.get("location_name") or meta.get("location") or meta.get("name") or "",
            }
        )
    return hits


def main():
    ap = argparse.ArgumentParser(description="RAG 점수 분포 프로브 → threshold 산출")
    ap.add_argument("--ollama-url", default="http://localhost:11434", help="Ollama base URL (RC_LLM_EMBEDDING_BASE_URL)")
    ap.add_argument("--embed-model", default="qwen3-embedding:8b", help="임베딩 모델 (RC_LLM_EMBEDDING_MODEL)")
    ap.add_argument("--qdrant-url", default="http://10.159.172.74:6333")
    ap.add_argument("--collection", default="robo_claw_former_w2_2f_2")
    ap.add_argument("--limit", type=int, default=10, help="질의당 상위 N개 조회")
    ap.add_argument("--queries-file", help='질의셋 JSON: [{"query":"...","expect":"..."}]')
    # 비대칭 임베딩: 에이전트(ollama.py embed_query)와 동일하게 qwen3-embedding 쿼리에
    # 지시문을 부착해 측정한다. --no-query-instruction 으로 끄면 이전(대칭) 방식과 A/B 비교.
    ap.add_argument("--query-instruction", default=(
        "Given a user's request, retrieve the most relevant stored robot "
        "knowledge such as place names, coordinates, and facts."),
        help="qwen3-embedding 쿼리에 붙일 지시문(에이전트 기본값과 동일)")
    ap.add_argument("--no-query-instruction", action="store_true",
                    help="쿼리 지시문 미부착(대칭 임베딩, 이전 방식)")
    args = ap.parse_args()

    def _wrap_query(text: str) -> str:
        m = (args.embed_model or "").lower()
        if not args.no_query_instruction and ("qwen3-embedding" in m or "qwen3_embedding" in m):
            return f"Instruct: {args.query_instruction}\nQuery: {text}"
        return text

    if args.queries_file:
        with open(args.queries_file, encoding="utf-8") as f:
            queries = json.load(f)
    else:
        queries = DEFAULT_QUERIES

    print(f"embed: {args.embed_model} @ {args.ollama_url}")
    print(f"qdrant: {args.collection} @ {args.qdrant_url}")
    _qi = "OFF(대칭)" if args.no_query_instruction else (
        "ON(비대칭 지시문)" if ("qwen3-embedding" in (args.embed_model or "").lower()) else "N/A(비 qwen3)")
    print(f"query instruction: {_qi}")
    print("=" * 78)

    correct_scores: list[float] = []
    wrong_above_correct: list[tuple[str, float, float]] = []

    for q in queries:
        query = q["query"]
        expect = q.get("expect", "")
        try:
            vec = embed(args.ollama_url, args.embed_model, _wrap_query(query))
            hits = qdrant_search(args.qdrant_url, args.collection, vec, args.limit)
        except Exception as e:  # noqa: BLE001
            print(f"[{query}] 오류: {e}")
            continue

        print(f"\n[Q] {query}   (기대 매치: '{expect}')")
        top_correct = None
        for i, h in enumerate(hits):
            hit_ok = expect and (expect in h["text"] or expect in str(h["name"]))
            mark = "  <-- 정답" if hit_ok and top_correct is None else ""
            if hit_ok and top_correct is None:
                top_correct = h["score"]
            print(f"   {i+1:2d}. score={h['score']:.3f}  type={h['type']:<18} name={h['name']:<10} {h['text']}{mark}")
        if top_correct is not None:
            correct_scores.append(top_correct)
            # 정답보다 위에 있는 오답(있으면 분리 곤란 신호)
            for h in hits:
                if h["score"] > top_correct and not (expect and (expect in h["text"] or expect in str(h["name"]))):
                    wrong_above_correct.append((query, h["score"], top_correct))
                    break
        else:
            print("   (정답 매치가 상위 결과에 없음 — expect 확인 또는 데이터 부재)")

    print("\n" + "=" * 78)
    if correct_scores:
        mn = min(correct_scores)
        mx = max(correct_scores)
        avg = sum(correct_scores) / len(correct_scores)
        print(f"정답 매치 score  min={mn:.3f}  avg={avg:.3f}  max={mx:.3f}  (n={len(correct_scores)})")
        suggested = max(0.0, round(mn - 0.05, 2))
        print(f"권장 threshold ≈ {suggested:.2f}  (정답 최저점 {mn:.3f}에서 여유 0.05 아래)")
        if wrong_above_correct:
            print("⚠️  정답보다 높은 오답이 있어 threshold만으로 분리가 어렵습니다:")
            for query, ws, cs in wrong_above_correct:
                print(f"    - [{query}] 오답 {ws:.3f} > 정답 {cs:.3f}")
            print("    → 타입 필터(이미 적용) 유지 + top_k 조정으로 보완하세요.")
    else:
        print("정답 매치를 한 건도 못 찾았습니다. 컬렉션명/임베더/데이터를 확인하세요.")


if __name__ == "__main__":
    main()
