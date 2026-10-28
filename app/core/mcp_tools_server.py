import json
import sys
import os
import re
import pickle
from pathlib import Path
import random
from typing import Dict, Any, List, Optional

from mcp.server.fastmcp import FastMCP
from PIL import Image

import torch
import torch.nn as nn
import torchvision.models as models
from torch.nn.functional import softmax

from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_community.tools import WikipediaQueryRun
from langchain_community.utilities import WikipediaAPIWrapper

try:
    from langchain_core.documents import Document
except ImportError:
    from langchain.schema import Document


# ── 경로 정의
BASE_DIR   = Path(__file__).resolve().parent
ASSETS_DIR = BASE_DIR.parent / "assets"

RAG_DB_PATH       = ASSETS_DIR / "rag_faiss_index"
BM25_INDEX_PATH   = ASSETS_DIR / "bm25_index.pkl"
RESTAURANT_DB_PATH = ASSETS_DIR / "restaurants.json"
KING_QUOTE_PATH   = ASSETS_DIR / "king_mbti.json"
CV_MODEL_PATH     = ASSETS_DIR / "landmark_classifier_best.pt"
CV_LABEL_PATH     = ASSETS_DIR / "class_labels.json"


# ── 전역 상태
RAG_DB       = None
BM25_PAYLOAD = None  # {"bm25": BM25Okapi, "chunks": list[dict]}
CV_MODEL     = None
CV_CLASS_NAMES = None
CV_TRANSFORMS  = None
DEVICE = torch.device("cpu")


# ── 토크나이저 (BM25 전용)
_TOKENIZE_FN = None

def _get_tokenizer():
    """Okt 명사 추출을 쓰고, 설치되지 않았으면 조사 제거 정규식으로 대체합니다."""
    global _TOKENIZE_FN
    if _TOKENIZE_FN is not None:
        return _TOKENIZE_FN

    try:
        from konlpy.tag import Okt
        okt = Okt()
        _TOKENIZE_FN = lambda text: okt.nouns(str(text))
        print("[MCP] Okt 토크나이저 로드 완료", file=sys.stderr)
    except Exception:
        _PARTICLE_RE = re.compile(
            r"(이|가|을|를|의|에|에서|으로|로|과|와|은|는|께서|께|한테|에게|도|만|까지|부터)$"
        )
        def _regex_tokenize(text: str) -> list[str]:
            tokens = []
            for t in str(text).split():
                t = _PARTICLE_RE.sub("", t).strip()
                if t:
                    tokens.append(t)
            return tokens
        _TOKENIZE_FN = _regex_tokenize
        print("[MCP] 정규식 토크나이저 사용 (Okt 미설치)", file=sys.stderr)

    return _TOKENIZE_FN


# ── DB 로더
def load_rag_db():
    global RAG_DB
    if RAG_DB is not None:
        return
    print("[MCP] FAISS RAG DB 로딩 중...", file=sys.stderr)
    if not RAG_DB_PATH.exists():
        print(f"❌ [MCP] RAG DB 경로 없음: {RAG_DB_PATH}", file=sys.stderr)
        return
    try:
        embeddings = HuggingFaceEmbeddings(
            model_name="BAAI/bge-m3",
            model_kwargs={"device": "cpu"},
            encode_kwargs={"normalize_embeddings": True},
        )
        RAG_DB = FAISS.load_local(
            str(RAG_DB_PATH),
            embeddings,
            allow_dangerous_deserialization=True,
        )
        print("   ✅ FAISS RAG DB 로드 완료.", file=sys.stderr)
    except Exception as e:
        print(f"❌ [MCP] FAISS 로드 실패: {e}", file=sys.stderr)


def load_bm25_index():
    """BM25 인덱스가 없으면 FAISS만 사용합니다."""
    global BM25_PAYLOAD
    if BM25_PAYLOAD is not None:
        return
    if not BM25_INDEX_PATH.exists():
        print(f"[MCP] BM25 인덱스 없음 ({BM25_INDEX_PATH}) → FAISS 단독 모드로 동작", file=sys.stderr)
        return
    try:
        with open(BM25_INDEX_PATH, "rb") as f:
            BM25_PAYLOAD = pickle.load(f)
        chunk_count = len(BM25_PAYLOAD.get("chunks", []))
        print(f"   ✅ BM25 인덱스 로드 완료 ({chunk_count:,}개 청크).", file=sys.stderr)
    except Exception as e:
        print(f"❌ [MCP] BM25 로드 실패: {e}", file=sys.stderr)


def load_restaurant_db() -> dict:
    try:
        return json.loads(RESTAURANT_DB_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"❌ [MCP] 맛집 DB 로드 실패: {e}", file=sys.stderr)
        return {}


def load_king_quotes() -> list:
    try:
        return json.loads(KING_QUOTE_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"❌ [MCP] 왕 명언 DB 로드 실패: {e}", file=sys.stderr)
        return []


def load_cv_model():
    global CV_MODEL, CV_CLASS_NAMES, CV_TRANSFORMS, DEVICE
    if CV_MODEL is not None:
        return
    print("[MCP] CV 모델 로딩 중...", file=sys.stderr)
    if not CV_MODEL_PATH.exists():
        print(f"❌ [MCP] CV 모델 파일 없음: {CV_MODEL_PATH}", file=sys.stderr)
        return
    if not CV_LABEL_PATH.exists():
        print(f"❌ [MCP] CV 라벨 파일 없음: {CV_LABEL_PATH}", file=sys.stderr)
        return
    try:
        with open(CV_LABEL_PATH, "r", encoding="utf-8") as f:
            labels_dict = json.load(f)
        CV_CLASS_NAMES = list(labels_dict.keys())
        num_classes = len(CV_CLASS_NAMES)
        if num_classes == 0:
            print("❌ [MCP] CV 라벨 0개", file=sys.stderr)
            return

        weights = models.EfficientNet_B0_Weights.DEFAULT
        model   = models.efficientnet_b0(weights=weights)
        num_ftrs = model.classifier[1].in_features
        model.classifier[1] = nn.Linear(num_ftrs, num_classes)
        model.load_state_dict(torch.load(CV_MODEL_PATH, map_location=DEVICE))
        model = model.to(DEVICE)
        model.eval()

        CV_MODEL      = model
        CV_TRANSFORMS = weights.transforms()
        print(f"   ✅ CV 모델 로드 완료 ({num_classes}개 라벨).", file=sys.stderr)
    except Exception as e:
        print(f"❌ [MCP] CV 모델 로드 실패: {e}", file=sys.stderr)
        raise e


# ── 쿼리 파싱 유틸

KNOWN_KINGS = [
    "태조", "정종", "태종", "세종", "문종", "단종", "세조", "예종", "성종",
    "연산군", "중종", "인종", "명종", "선조", "광해군", "인조", "효종", "현종",
    "숙종", "경종", "영조", "정조", "순조", "헌종", "철종",
]

# 대수 → 왕 이름 매핑 (조선 1~25대)
KING_ORDER_MAP: Dict[int, str] = {
    1: "태조", 2: "정종", 3: "태종", 4: "세종", 5: "문종",
    6: "단종", 7: "세조", 8: "예종", 9: "성종", 10: "연산군",
    11: "중종", 12: "인종", 13: "명종", 14: "선조", 15: "광해군",
    16: "인조", 17: "효종", 18: "현종", 19: "숙종", 20: "경종",
    21: "영조", 22: "정조", 23: "순조", 24: "헌종", 25: "철종",
}

ACCESSION_KEYWORDS = {"즉위년", "즉위 원년", "원년"}

# "최초/처음/언제" 질문 감지 키워드
TEMPORAL_KEYWORDS  = {"최초", "처음", "언제", "첫", "시작", "기원", "유래", "최초로", "처음으로"}


def parse_query_metadata(query: str) -> dict:
    """
    쿼리에서 왕 이름·대수·연도·월 필터를 정규식으로 추출합니다.

    예: {"king_name": "세종", "year_reign_int": 4, "month_int": 11.0}
        {"king_name": "광해군", "king_order": 15}
    """
    meta = {}

    for king in KNOWN_KINGS:
        if king in query:
            meta["king_name"] = king
            break

    ordinal_question = bool(re.search(r"몇[\s]*(대|번째|번)", query))
    ordinal_m = re.search(r"(?<![\d년])(\d{1,2})[\s]*(대|번째)[\s]*(왕|임금|군주)?", query)
    if ordinal_m:
        order_num = int(ordinal_m.group(1))
        if 1 <= order_num <= 25 and order_num in KING_ORDER_MAP:
            resolved_king = KING_ORDER_MAP[order_num]
            if "king_name" not in meta:
                meta["king_name"] = resolved_king
            meta["king_order"] = order_num
            print(f"[RAG] 대수 파싱: {order_num}대 → {resolved_king}", file=sys.stderr)

    # "광해군은 몇대 왕이야"처럼 왕 이름으로 대수를 묻는 경우
    if ordinal_question and "king_name" in meta and "king_order" not in meta:
        for order_num, king_name in KING_ORDER_MAP.items():
            if king_name == meta["king_name"]:
                meta["king_order"] = order_num
                break

    if any(kw in query for kw in ACCESSION_KEYWORDS):
        meta["is_accession_year"] = True
        meta["year_reign_int"]    = -1
        _parse_month(query, meta)
        return meta

    m = re.search(r"(\d{4})년", query)
    if m:
        year = int(m.group(1))
        if 1392 <= year <= 1910:
            meta["year_solar"] = year

    # 서력 연도나 대수로 쓰인 숫자는 재위년으로 해석하지 않음
    if "year_solar" not in meta and "king_order" not in meta:
        m = re.search(r"(?<!\d)(\d{1,2})년", query)
        if m:
            meta["year_reign_int"] = int(m.group(1))

    _parse_month(query, meta)

    return meta


def _parse_month(query: str, meta: dict) -> None:
    """윤달은 month_int에 0.5를 더해 구분합니다."""
    m = re.search(r"윤\s*(\d+)월", query)
    if m:
        meta["month_int"] = int(m.group(1)) + 0.5
        return
    m = re.search(r"(\d+)월", query)
    if m:
        meta["month_int"] = float(m.group(1))


def is_temporal_query(query: str) -> bool:
    """'최초/처음/언제' 류의 시간 순서가 중요한 질문인지 감지합니다."""
    return any(kw in query for kw in TEMPORAL_KEYWORDS)


def extract_extra_keywords(query: str, meta_filter: dict) -> str:
    """
    필터로 쓰인 왕 이름·연도·월과 불용어를 제거하고 남은 키워드를 반환합니다.
    예: "1450년 훈민정음 창제" → "훈민정음 창제", "1452년에 무슨 일이" → ""
    """
    if not meta_filter:
        return ""

    stripped = query

    for king in KNOWN_KINGS:
        stripped = stripped.replace(king, "")
    stripped = stripped.replace("대왕", "").replace("임금", "").replace("왕", "")

    stripped = re.sub(r"\d{4}년", "", stripped)
    stripped = re.sub(r"윤?\s*\d+월", "", stripped)
    stripped = re.sub(r"(?<!\d)\d{1,2}년", "", stripped)

    for kw in ACCESSION_KEYWORDS:
        stripped = stripped.replace(kw, "")

    stopwords = {
        "무슨", "어떤", "있어", "있었어", "알려줘", "뭐야", "기록",
        "관련", "내용", "대해", "에서", "이야", "인지", "했어",
        "일이", "일은", "때", "에", "의", "이", "가", "을", "를",
        "에게", "께서", "하고", "이랑", "부터", "까지",
        "어느", "누가", "언제", "어디", "왜", "어떻게", "뭐",
        "요약", "정리", "설명", "알아봐", "찾아봐", "검색", "하나만", "중요한",
        "서기", "환산", "년도",
    }
    tokens = [t.strip() for t in re.split(r"\s+|[?.,!]", stripped) if t.strip()]
    meaningful = [t for t in tokens if len(t) >= 2 and t not in stopwords]

    # "수원 화성" → "수원화성"처럼 붙여 쓴 고유명사도 매칭되도록 인접 토큰을 결합
    expanded = list(meaningful)
    for i in range(len(meaningful) - 1):
        compound = meaningful[i] + meaningful[i + 1]
        if len(compound) >= 3:
            expanded.append(compound)

    return " ".join(expanded)


# ── 검색 내부 함수

def _chunks_scan(meta_filter: dict) -> list:
    """
    전체 청크를 직접 스캔해 meta_filter를 만족하는 문서를 모두 반환합니다.
    청크 메타데이터의 int/str 혼재를 고려해 문자열로 비교합니다.
    """
    if BM25_PAYLOAD is None:
        return []

    king_order_filter = meta_filter.get("king_order")
    base_filter = {k: v for k, v in meta_filter.items() if k != "king_order"}

    results = []
    for chunk in BM25_PAYLOAD["chunks"]:
        m = chunk.get("metadata", {})

        if base_filter:
            if not all(str(m.get(k, "")).strip() == str(v).strip() for k, v in base_filter.items()):
                continue

        if king_order_filter is not None:
            chunk_order = m.get("king_order", m.get("order", ""))
            if str(chunk_order).strip() != str(king_order_filter).strip():
                # 청크에 king_order가 없으면 king_name으로 확인
                chunk_king = m.get("king_name", "")
                expected_king = KING_ORDER_MAP.get(king_order_filter, "")
                if chunk_king != expected_king:
                    continue

        try:
            from langchain_core.documents import Document
        except ImportError:
            from langchain.schema import Document
        results.append(Document(
            page_content=chunk["page_content"],
            metadata=chunk["metadata"],
        ))
    return results


def _faiss_search(query: str, meta_filter: dict, limit: int) -> list:
    """
    메타 필터가 있으면 k=500으로 넉넉히 검색한 뒤 직접 필터링하고,
    결과가 부족하면 MMR 검색으로 보완합니다.
    """
    results = []
    seen    = set()

    if meta_filter:
        # LangChain FAISS filter는 상위 k개 안에서만 거르므로 후보를 넉넉히 가져와 직접 필터링
        try:
            candidates = RAG_DB.similarity_search(query, k=500)
            for doc in candidates:
                m = doc.metadata
                if all(m.get(k) == v for k, v in meta_filter.items()):
                    if doc.page_content not in seen:
                        results.append(doc)
                        seen.add(doc.page_content)
            print(f"[RAG] 메타 필터 검색: {len(results)}개 / 후보 {len(candidates)}개 ({meta_filter})", file=sys.stderr)
        except Exception as e:
            print(f"[RAG] 메타 필터 검색 실패: {e}", file=sys.stderr)

    if len(results) < limit:
        try:
            mmr_results = RAG_DB.max_marginal_relevance_search(
                query,
                k=limit * 2,
                fetch_k=50,
                lambda_mult=0.7,
            )
            for doc in mmr_results:
                if doc.page_content not in seen:
                    results.append(doc)
                    seen.add(doc.page_content)
        except Exception as e:
            print(f"[RAG] MMR 검색 실패: {e}", file=sys.stderr)

    return results


def _bm25_search(query: str, limit: int) -> list:
    if BM25_PAYLOAD is None:
        return []

    try:
        tokenize = _get_tokenizer()
        tokens   = tokenize(query)

        if not tokens:
            return []

        bm25   = BM25_PAYLOAD["bm25"]
        chunks = BM25_PAYLOAD["chunks"]
        scores = bm25.get_scores(tokens)

        top_indices = sorted(
            [i for i in range(len(scores)) if scores[i] > 0],
            key=lambda i: scores[i],
            reverse=True,
        )[: limit * 2]

        results = []
        for idx in top_indices:
            chunk = chunks[idx]
            doc   = Document(
                page_content=chunk["page_content"],
                metadata=chunk["metadata"],
            )
            results.append(doc)

        return results[:limit]

    except Exception as e:
        print(f"[BM25] 검색 실패: {e}", file=sys.stderr)
        return []


def _rrf_merge(faiss_docs: list, bm25_docs: list, limit: int, k: int = 60) -> list:
    """Reciprocal Rank Fusion (k=60)."""
    scores  : dict[str, float] = {}
    doc_map : dict[str, Document] = {}

    for rank, doc in enumerate(faiss_docs):
        key = doc.page_content
        scores[key]  = scores.get(key, 0.0) + 1.0 / (rank + k)
        doc_map[key] = doc

    for rank, doc in enumerate(bm25_docs):
        key = doc.page_content
        scores[key]  = scores.get(key, 0.0) + 1.0 / (rank + k)
        doc_map[key] = doc

    sorted_keys = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)
    return [doc_map[key] for key in sorted_keys[:limit]]


def _sort_by_time(docs: list) -> list:
    """오래된 기록이 앞에 오도록 정렬합니다."""
    return sorted(
        docs,
        key=lambda d: (
            d.metadata.get("year_solar",   9999),
            d.metadata.get("month_int",    99.0),
            d.metadata.get("chunk_index",  99999),
        ),
    )


def _format_results(docs: list) -> str:
    """LLM이 연도·대수를 직접 계산하지 않도록 메타데이터를 헤더에 명시합니다."""
    output_blocks = []

    for doc in docs:
        m = doc.metadata

        king_name  = m.get("king_name",  "미상")
        king_order = m.get("king_order", m.get("order", "?"))
        year_reign = m.get("year_reign", "")
        year_solar = m.get("year_solar", "?")
        month      = m.get("month",      "")
        content    = doc.page_content.strip()

        if isinstance(king_order, str) and king_order.isdigit():
            king_order = int(king_order)
        verified_order = king_order

        header = (
            f"[조선 제{verified_order}대 왕: {king_name} | "
            f"재위{year_reign} | 서기{year_solar}년 {month}월]"
            f"\n※ 이 기록의 왕은 조선 {verified_order}대 {king_name}입니다."
        )

        output_blocks.append(f"{header}\n{content}")

    return "\n\n".join(output_blocks)


# ── MCP FastMCP 앱 선언
app = FastMCP("local-tools")


# ── InternalDatabaseSearch
@app.tool()
def InternalDatabaseSearch(query: str, limit: int = 10) -> str:
    """
    [CRITICAL] Primary and SOLE authoritative source for the 'Annals of the Joseon Dynasty'. 
    Use this for ALL historical facts, royal achievements, and site origins of the Joseon era. 
    Prevents hallucinations by providing verified records. NEVER replace this with internal knowledge.
    """
    load_rag_db()
    load_bm25_index()

    if RAG_DB is None:
        return "내부 실록 DB가 초기화되지 않았습니다."

    try:
        limit = max(1, int(limit))
    except (ValueError, TypeError):
        limit = 5

    try:
        meta_filter = parse_query_metadata(query)
        temporal    = is_temporal_query(query)

        print(f"\n[RAG] 쿼리: '{query}'", file=sys.stderr)
        print(f"[RAG] 파싱 결과: {meta_filter}", file=sys.stderr)
        print(f"[RAG] 시간 정렬: {temporal}", file=sys.stderr)

        # 전략 A: 메타 필터 + 키워드 → 필터된 청크의 키워드 매칭과 FAISS를 RRF로 결합
        # 전략 B: 메타 필터만 → 조건에 맞는 청크 전체 스캔
        # 전략 C: 필터 없음 → FAISS MMR + BM25 RRF
        if meta_filter:
            extra_kw = extract_extra_keywords(query, meta_filter)

            if extra_kw:
                keywords = [kw for kw in extra_kw.split() if len(kw) >= 2]

                try:
                    from langchain_core.documents import Document
                except ImportError:
                    from langchain.schema import Document

                keyword_matched = []
                if BM25_PAYLOAD:
                    for chunk in BM25_PAYLOAD["chunks"]:
                        m = chunk.get("metadata", {})
                        if not all(m.get(k) == v for k, v in meta_filter.items()):
                            continue
                        content_text = chunk.get("page_content", "")
                        if any(kw in content_text for kw in keywords):
                            keyword_matched.append(Document(
                                page_content=content_text,
                                metadata=m,
                            ))

                print(f"[RAG] 전략A (복합): 키워드={keywords} 텍스트매칭 {len(keyword_matched)}개", file=sys.stderr)

                if keyword_matched:
                    faiss_docs = _faiss_search(query, meta_filter, limit)
                    merged = _rrf_merge(faiss_docs, keyword_matched, limit=limit * 2)
                else:
                    scan_docs = _chunks_scan(meta_filter)
                    print(f"[RAG] 전략A fallback: chunks 스캔 {len(scan_docs)}개", file=sys.stderr)
                    merged = scan_docs

            else:
                scan_docs = _chunks_scan(meta_filter)
                print(f"[RAG] 전략B (필터): chunks 스캔 {len(scan_docs)}개", file=sys.stderr)
                merged = scan_docs

        else:
            faiss_docs = _faiss_search(query, {}, limit)
            bm25_docs  = _bm25_search(query, limit)
            print(f"[RAG] 전략C (하이브리드): FAISS {len(faiss_docs)}개 / BM25 {len(bm25_docs)}개", file=sys.stderr)
            if bm25_docs:
                merged = _rrf_merge(faiss_docs, bm25_docs, limit=limit * 2)
            else:
                merged = faiss_docs

        if temporal:
            merged = _sort_by_time(merged)
            print(f"[RAG] 시간 정렬 완료 → 가장 오래된 기록 우선", file=sys.stderr)

        # 결과가 없으면 전략 C로 재시도
        if not merged:
            print(f"[RAG] 결과 0개 → 전략C(Hybrid) 자동 fallback 실행", file=sys.stderr)
            faiss_fb = _faiss_search(query, {}, limit)
            bm25_fb  = _bm25_search(query, limit)
            if bm25_fb:
                merged = _rrf_merge(faiss_fb, bm25_fb, limit=limit * 2)
            else:
                merged = faiss_fb
            if temporal:
                merged = _sort_by_time(merged)

        final = merged[:limit]

        if not final:
            return "내부 실록 DB에서 관련 기록을 찾지 못했습니다."

        return _format_results(final)

    except Exception as e:
        print(f"❌ [RAG] 검색 오류: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return f"RAG DB 검색 오류: {e}"


# ── search_nearby_restaurants
@app.tool(name="search_nearby_restaurants")
def search_nearby_restaurants(location: str) -> str:
    db      = load_restaurant_db()
    results = db.get(location, [])
    if not results:
        return f"'{location}' 근처 맛집 정보를 찾지 못했습니다."
    return json.dumps(results, ensure_ascii=False, indent=2)


# ── ExternalWebSearch
@app.tool()
def ExternalWebSearch(query: str) -> str:
    """
    Accesses Wikipedia for general knowledge or events OUTSIDE the Joseon era (e.g., modern history, global facts).
    [STRICT RULE] USE ONLY when the user explicitly uses keywords like "web search", "external", or "find more online".
    FORBIDDEN to use as an automatic fallback for internal database failures.
    """
    try:
        api_wrapper = WikipediaAPIWrapper(
            lang="ko",
            top_k_results=1,
            doc_content_chars_max=500,
        )
        wiki_tool = WikipediaQueryRun(api_wrapper=api_wrapper)
        print(f"[Wikipedia] '{query}' 검색 중...", file=sys.stderr)
        return wiki_tool.run(query)
    except Exception as e:
        print(f"❌ 위키백과 검색 실패: {e}", file=sys.stderr)
        return f"위키백과 검색 중 오류 발생: {e}"


# ── KingQuoteProvider
@app.tool()
def KingQuoteProvider() -> str:
    data = load_king_quotes()
    if not data:
        return json.dumps({"error": "왕 명언 DB 로딩 실패"})
    item = random.choice(data)
    return json.dumps(item, ensure_ascii=False, indent=2)


# ── HeritageImageRecognizer
@app.tool(name="HeritageImageRecognizer.identify_landmark")
def identify_landmark(image_path: str) -> Dict[str, Any]:
    """
    On-device CV 모델(EfficientNet-B0, PyTorch)로 이미지를 분류하고
    유적지 라벨명과 신뢰도를 반환합니다.
    """
    load_cv_model()
    if not CV_MODEL or not CV_CLASS_NAMES or not CV_TRANSFORMS:
        return {"error": "CV 모델이 초기화되지 않았습니다."}

    print(f"\n[CV] '{image_path}' 분석 중...", file=sys.stderr)
    try:
        img        = Image.open(image_path).convert("RGB")
        img_tensor = CV_TRANSFORMS(img).unsqueeze(0).to(DEVICE)

        with torch.no_grad():
            outputs = CV_MODEL(img_tensor)

        probabilities              = softmax(outputs[0], dim=0)
        confidence, label_index   = torch.max(probabilities, 0)
        label_name                = CV_CLASS_NAMES[label_index.item()]
        confidence_value          = confidence.item() * 100

        print(f"   ✅ CV 결과: '{label_name}' (신뢰도: {confidence_value:.2f}%)", file=sys.stderr)
        return {"landmark_name": label_name, "confidence": float(confidence_value)}

    except Exception as e:
        print(f"❌ CV 예측 실패: {e}", file=sys.stderr)
        return {"error": f"CV 예측 실패: {e}"}


# ── MCP 서버 실행
if __name__ == "__main__":
    print("[MCP Tools Server] 'local-tools' 서버를 stdio 모드로 시작합니다...", file=sys.stderr)
    app.run()