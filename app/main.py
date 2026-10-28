import os
import shutil
import json
import uuid
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List
import uvicorn
from contextlib import asynccontextmanager

os.environ["TOKENIZERS_PARALLELISM"] = "false"

BASE_DIR = Path(__file__).resolve().parent
TEMP_UPLOAD_DIR = BASE_DIR / "temp_uploads"
ROOT_DIR = BASE_DIR.parent

from app.core import agent as agent_module

# agent 모듈 import 시 .env가 로드되므로 그 이후에 읽는다
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "furiosa")

APP_STATE = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("=" * 50)
    print("🚀 API 서버 부팅을 시작합니다...")

    TEMP_UPLOAD_DIR.mkdir(exist_ok=True)

    agent_executor = agent_module.create_super_agent(LLM_PROVIDER)

    try:
        await agent_executor.agent.setup_mcp_servers()

        # 첫 요청 지연을 없애기 위해 BGE-M3/FAISS/BM25를 미리 로드
        print("[Warmup] FAISS/BM25/BGE-M3 미리 로드 중... (약 10~20초)")
        await agent_executor.agent._call_tool(
            "local-tools_InternalDatabaseSearch",
            {"query": "경복궁", "limit": 1}
        )
        print("[Warmup] ✅ 완료")
    except Exception as e:
        print(f"❌ MCP Tool 서버 연결 실패: {e}")
        agent_executor = None

    APP_STATE["agent"] = agent_executor

    print("=" * 50)
    if not agent_executor:
        print("에이전트 생성 또는 Tool 서버 연결에 실패했습니다.")
    else:
        print("준비 완료. API 서버가 요청을 기다립니다.")
        print("=" * 50)

    yield

    if agent_executor:
        await agent_executor.agent.close_mcp_session()

    print("\nAPI 서버를 종료합니다.")

app = FastAPI(title="WelKome Super Agent API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=ROOT_DIR / "frontend" / "static"), name="static")


@app.get("/", response_class=FileResponse, summary="[Frontend] 메인 HTML 제공")
async def read_index():
    return ROOT_DIR / "frontend" / "index.html"


@app.post("/predict_image", summary="[시나리오 1] 이미지로 메인 화면 구성")
async def predict_image_and_get_main_screen(file: UploadFile = File(...)):
    """이미지를 CV 모델로 인식한 뒤 유적지 설명과 실록 요약을 반환합니다."""
    agent = APP_STATE.get("agent")
    if not agent:
        raise HTTPException(status_code=503, detail="에이전트가 준비되지 않았습니다.")

    suffix = Path(file.filename or "").suffix.lower()[:10]
    temp_path = TEMP_UPLOAD_DIR / f"{uuid.uuid4().hex}{suffix}"
    try:
        with temp_path.open("wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"파일 저장 실패: {e}")
    finally:
        file.file.close()

    try:
        cv_label = await agent.agent.identify_from_image(str(temp_path))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"CV 도구 호출 실패: {e}")
    finally:
        temp_path.unlink(missing_ok=True)

    if not cv_label:
        print("CV 결과: None (신뢰도 낮음 또는 인식 실패)")
        raise HTTPException(status_code=404, detail="죄송합니다. 문화재를 찾을 수 없습니다. 빠른 시일 내에 업로드하겠습니다.")

    try:
        summary = await agent.agent.get_main_screen_summary(cv_label)
        return {
            "cv_label": cv_label,
            "description": summary.get("description", ""),
            "sillok_summary": summary.get("sillok_summary", ""),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"에이전트 호출 실패: {e}")


@app.get("/restaurants/{landmark_name}", summary="[시나리오 2] 맛집 버튼 클릭")
async def get_restaurant_info(landmark_name: str):
    agent = APP_STATE.get("agent")
    if not agent:
        raise HTTPException(status_code=503, detail="에이전트가 준비되지 않았습니다.")

    print(f"\n[Orchestrator] 시나리오 2: 맛집 도구 호출 ('{landmark_name}')")

    try:
        response_data = await agent.agent.get_nearby_restaurants(landmark_name)
        if isinstance(response_data, str):
            response_data = json.loads(response_data)
        return {"landmark": landmark_name, "agent_response": response_data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"맛집 도구 호출 실패: {e}")


class ChatHistoryItem(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    input: str
    chat_history: List[ChatHistoryItem] = []

class ChatResponse(BaseModel):
    output: str
    chat_history: List[ChatHistoryItem]


@app.post("/chat", response_model=ChatResponse, summary="[시나리오 3] 챗봇 세션")
async def chat_with_agent(request: ChatRequest):
    agent = APP_STATE.get("agent")
    if not agent:
        raise HTTPException(status_code=503, detail="에이전트가 준비되지 않았습니다.")

    print(f"\n[Orchestrator] 시나리오 3 (챗봇): \"{request.input}\"")

    try:
        response = await agent.ainvoke({
            "input": request.input,
            "chat_history": request.chat_history
        })

        new_history = request.chat_history + [
            ChatHistoryItem(role="human", content=request.input),
            ChatHistoryItem(role="ai", content=response['output'])
        ]
        return ChatResponse(output=response['output'], chat_history=new_history)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"에이전트 호출 실패: {e}")


@app.get("/loading_quote", summary="[시나리오 0] 로딩 화면 명언")
async def get_loading_quote():
    agent = APP_STATE.get("agent")
    if not agent:
        raise HTTPException(status_code=503, detail="에이전트가 준비되지 않았습니다.")

    print("\n[Orchestrator] 시나리오 0: 왕 명언 도구 호출")

    try:
        response_data = await agent.agent.get_loading_quote()
        if isinstance(response_data, str):
            response_data = json.loads(response_data)
        return {"agent_response": response_data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"왕 명언 도구 호출 실패: {e}")


if __name__ == "__main__":
    print("🖥️ Uvicorn 서버를 http://127.0.0.1:8000 에서 시작합니다.")
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
