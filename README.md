# WelKome AI 도슨트

> 유적지 사진을 찍으면 조선왕조실록을 근거로 역사를 안내하는 AI 도슨트 서비스

## 1. 프로젝트 구조

```
welKome_app/
├── app/
│   ├── main.py                  # FastAPI 서버 진입점
│   ├── .env                     # 환경 변수 (직접 생성, 저장소 미포함)
│   ├── core/
│   │   ├── agent.py             # LLM + MCP 에이전트
│   │   └── mcp_tools_server.py  # MCP 툴 서버 (RAG, CV, 맛집, 명언)
│   └── assets/                  # 모델·인덱스·데이터 (저장소 미포함, 별도 배포)
├── frontend/
│   ├── index.html
│   └── static/
└── requirements.txt
```

## 2. 시스템 요구사항

| 항목 | 권장 |
|------|------|
| Python | 3.11 |
| RAM | 16GB 이상 |
| 저장 공간 | 10GB 이상 (BGE-M3 모델 캐시 포함) |
| Java | KoNLPy Okt용 (없으면 정규식 토크나이저로 대체) |

```bash
# macOS
brew install openjdk
# Ubuntu
sudo apt-get install -y default-jdk
```

## 3. 설치

```bash
git clone <repository_url>
cd welKome_app
python -m venv demo_env
source demo_env/bin/activate        # Windows: demo_env\Scripts\activate
pip install -r requirements.txt
```

## 4. 에셋 배치

`app/assets/`에 다음 파일이 있어야 합니다. 저장소에는 포함되어 있지 않으므로 별도로 전달받아 넣습니다.

```
app/assets/
├── rag_faiss_index/
│   ├── index.faiss
│   └── index.pkl
├── bm25_index.pkl
├── landmark_classifier_best.pt
├── class_labels.json
├── restaurants.json
└── king_mbti.json
```

첫 실행 시 BGE-M3 임베딩 모델(`BAAI/bge-m3`)과 EfficientNet-B0 가중치를 인터넷에서 내려받습니다.

## 5. 환경 변수

`app/.env` 파일을 만듭니다.

```env
# "openai" 또는 "furiosa"
LLM_PROVIDER=openai

OPENAI_API_KEY=
OPENAI_MODEL_NAME=gpt-4o

FURIOSA_API_BASE=
FURIOSA_API_KEY=
FURIOSA_MODEL_NAME=
```

## 6. 실행

```bash
python -m app.main
```

브라우저에서 `http://127.0.0.1:8000` 에 접속합니다.

## 7. API

| 메서드 | 경로 | 설명 |
|--------|------|------|
| `GET` | `/` | 메인 화면 |
| `POST` | `/predict_image` | 유적지 이미지 → CV 인식 + 실록 요약 |
| `GET` | `/restaurants/{landmark_name}` | 유적지 근처 맛집 |
| `POST` | `/chat` | 챗봇 대화 |
| `GET` | `/loading_quote` | 로딩 화면용 왕 명언 |

```bash
curl -X POST http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"input": "세종대왕의 건강이 어떠했어?", "chat_history": []}'
```

## 8. 아키텍처

```
FastAPI (main.py)
  └─ MCPWelKomeAgent (agent.py) — OpenAI 호환 LLM + 영구 MCP 세션
       └─ MCP Tools Server (mcp_tools_server.py)
            ├─ InternalDatabaseSearch   FAISS(BGE-M3) + BM25 하이브리드 검색
            ├─ search_nearby_restaurants
            ├─ ExternalWebSearch        사용자가 명시적으로 요청할 때만
            ├─ HeritageImageRecognizer  EfficientNet-B0
            └─ KingQuoteProvider
```

| 질문 유형 | 검색 전략 | 예시 |
|----------|------|------|
| 연도/왕/월 + 키워드 | 필터된 청크 키워드 매칭 + FAISS, RRF | "세종대왕 건강" |
| 연도/왕/월만 | 조건에 맞는 청크 전체 스캔 | "1452년에 무슨 일이" |
| 개념/고유명사 | FAISS MMR + BM25, RRF | "경복궁 역사" |

## 9. 문제 해결

- **`Failed to parse JSONRPC message from server`**: `mcp_tools_server.py`에서 stdout으로 출력하면 MCP 통신이 깨집니다. `print()`에는 `file=sys.stderr`를 사용합니다.
- **`FAISS 로드 실패`**: `app/assets/rag_faiss_index/`에 `index.faiss`, `index.pkl`이 있는지 확인합니다.
- **`Unable to locate a Java Runtime`**: Java 설치 후 터미널을 재시작합니다. Java가 없어도 서버는 동작합니다.
