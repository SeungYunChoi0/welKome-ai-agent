# WelKome-AI-Agent
> Take a photo of a heritage site and get its history explained, grounded in the Annals of the Joseon Dynasty (조선왕조실록).

## 1. Project Structure

```
welKome_app/
├── app/
│   ├── main.py                  # FastAPI entry point
│   ├── .env                     # Environment variables (create manually, not in the repo)
│   ├── core/
│   │   ├── agent.py             # LLM + MCP agent
│   │   └── mcp_tools_server.py  # MCP tool server (RAG, CV, restaurants, quotes)
│   └── assets/                  # Models, indexes, data (not in the repo, distributed separately)
├── frontend/
│   ├── index.html
│   └── static/
└── requirements.txt
```

## 2. Requirements

| Item | Recommended |
|------|------|
| Python | 3.11 |
| RAM | 16GB or more |
| Disk | 10GB or more (including the BGE-M3 model cache) |
| Java | For the KoNLPy Okt tokenizer (falls back to a regex tokenizer if missing) |

```bash
# macOS
brew install openjdk
# Ubuntu
sudo apt-get install -y default-jdk
```

## 3. Installation

```bash
git clone <repository_url>
cd welKome_app
python -m venv demo_env
source demo_env/bin/activate        # Windows: demo_env\Scripts\activate
pip install -r requirements.txt
```

## 4. Assets

The following files are not included in the repository and must be obtained separately.

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

frontend/static/images/*_menu.*     # Restaurant menu photos
```

On first run, the BGE-M3 embedding model (`BAAI/bge-m3`) and EfficientNet-B0 weights are downloaded from the internet.

## 5. Environment Variables

Create `app/.env`:

```env
# "openai" or "furiosa"
LLM_PROVIDER=openai

OPENAI_API_KEY=
OPENAI_MODEL_NAME=gpt-4o

FURIOSA_API_BASE=
FURIOSA_API_KEY=
FURIOSA_MODEL_NAME=
```

## 6. Run

```bash
python -m app.main
```

Open `http://127.0.0.1:8000` in a browser.

To accept external connections on a server:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## 7. API

| Method | Path | Description |
|--------|------|------|
| `GET` | `/` | Main page |
| `POST` | `/predict_image` | Heritage site photo → CV recognition + Annals summary |
| `GET` | `/restaurants/{landmark_name}` | Restaurants near the site |
| `POST` | `/chat` | Chatbot conversation |
| `GET` | `/loading_quote` | Random king quote for the loading screen |

```bash
curl -X POST http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"input": "세종대왕의 건강이 어떠했어?", "chat_history": []}'
```

## 8. Architecture

```
FastAPI (main.py)
  └─ MCPWelKomeAgent (agent.py) — OpenAI-compatible LLM + persistent MCP session
       └─ MCP Tools Server (mcp_tools_server.py)
            ├─ InternalDatabaseSearch   Hybrid search: FAISS (BGE-M3) + BM25
            ├─ search_nearby_restaurants
            ├─ ExternalWebSearch        Only when the user explicitly asks
            ├─ HeritageImageRecognizer  EfficientNet-B0
            └─ KingQuoteProvider
```

| Query type | Search strategy | Example |
|----------|------|------|
| Year/king/month + keyword | Keyword match within filtered chunks + FAISS, fused with RRF | "세종대왕 건강" |
| Year/king/month only | Full scan of chunks matching the filter | "1452년에 무슨 일이" |
| Concept / proper noun | FAISS MMR + BM25, fused with RRF | "경복궁 역사" |

## 9. Troubleshooting

- **`Failed to parse JSONRPC message from server`**: Printing to stdout in `mcp_tools_server.py` breaks MCP communication. Use `file=sys.stderr` for every `print()`.
- **`FAISS 로드 실패`** (FAISS load failed): Check that `index.faiss` and `index.pkl` exist in `app/assets/rag_faiss_index/`.
- **`Unable to locate a Java Runtime`**: Restart the terminal after installing Java. The server still runs without Java.
