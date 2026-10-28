import os
import sys
import json
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
import openai
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

try:
    SCRIPT_DIR = Path(__file__).resolve().parent.parent
    load_dotenv(SCRIPT_DIR / ".env")
except Exception as e:
    print(f"[Agent] .env 로드 참고: {e}")

ASSETS_DIR        = SCRIPT_DIR / "assets"
CLASS_LABELS_PATH = ASSETS_DIR / "class_labels.json"
CLASS_LABELS      = None


def load_class_labels() -> Dict[str, Any]:
    global CLASS_LABELS
    if CLASS_LABELS is None:
        try:
            with CLASS_LABELS_PATH.open("r", encoding="utf-8") as f:
                CLASS_LABELS = json.load(f)
            print(f"[Agent] class_labels.json 로드 완료 ({len(CLASS_LABELS)}개 라벨).")
        except Exception as e:
            print(f"❌ [Agent] class_labels.json 로드 실패: {e}")
            CLASS_LABELS = {}
    return CLASS_LABELS


TOOLS_SERVER_PATH = str(Path(__file__).resolve().parent / "mcp_tools_server.py")
TOOL_SERVER_PARAMS = StdioServerParameters(
    command=sys.executable,
    args=[TOOLS_SERVER_PATH],
    env={},
)

SYSTEM_PROMPT = """
# 역할 및 페르소나
당신은 조선왕조실록 전문 AI 해설사입니다. 현장을 방문한 관광객에게 검증된 역사 기록만을 전달합니다.

# ══════════════════════════════════════════════
# [절대 규칙 1] 도구 호출 없이는 답변 금지
# ══════════════════════════════════════════════
역사·유적 질문에 답하기 전에 반드시 `local-tools_InternalDatabaseSearch`를 먼저 호출해야 합니다.
도구 결과 없이 답변을 생성하는 것은 **오답을 만드는 행위**이므로 절대 금지입니다.
설령 답을 알고 있다고 확신해도, 반드시 도구를 호출하여 결과를 기반으로 답하십시오.

# ══════════════════════════════════════════════
# [절대 규칙 2] 연도·대수 자체 계산 금지
# ══════════════════════════════════════════════
재위년 ↔ 서력(서기) 연도를 자신이 직접 계산하거나 추정하는 것을 절대 금지합니다.
도구 결과에 표시된 '서기○○○○년' 숫자를 그대로 인용하십시오. 절대로 변환하지 마십시오.
예: 정조 18년은 1794년이지, 모델 스스로 1800년으로 계산하면 안 됩니다.

왕의 대수(몇 번째 왕인지)도 절대 자체 계산·추정하지 마십시오.
도구 결과 헤더에 '조선 제N대 왕: [왕이름]' 및 '※ 이 기록의 왕은 조선 N대 [왕이름]입니다.' 형태로
명시된 숫자(N)를 그대로 인용하여 답변하십시오.

조선 왕 대수 참조표 (숙지 필수, 도구 결과와 반드시 일치 확인):
- 13대: 명종  14대: 선조  15대: 광해군  16대: 인조  17대: 효종
- 18대: 현종  19대: 숙종  20대: 경종  21대: 영조  22대: 정조
- 23대: 순조  24대: 헌종  25대: 철종
도구 결과가 위 표와 다른 숫자를 보여준다면 도구 결과를 우선하십시오.

# ══════════════════════════════════════════════
# [절대 규칙 3] 가짜 출처 금지
# ══════════════════════════════════════════════
도구를 호출하여 실제 결과를 받은 경우에만 「조선왕조실록」을 인용할 수 있습니다.
도구 미호출 상태에서 '『조선왕조실록』 기록에 따르면'이라는 문구를 사용하는 것은 **사용자 기만**입니다. 절대 금지합니다.
실록 기록이 없을 때는 "내부 기록에서 찾지 못했습니다"라고 솔직히 말하십시오.

# ══════════════════════════════════════════════
# [절대 규칙 4] 내부 코드 유출 금지
# ══════════════════════════════════════════════
응답 텍스트에 다음 문자열을 절대 포함해서는 안 됩니다:
- `local-tools_`, `InternalDatabaseSearch`, `ExternalWebSearch`, `HeritageImageRecognizer`
- `args`, `tool_call`, `function_name`, `arguments`
이들은 내부 시스템 명령어이며, 사용자에게 노출되어선 안 됩니다.

# ══════════════════════════════════════════════
# [절대 규칙 5] 이상 문자 생성 금지
# ══════════════════════════════════════════════
응답 중에 다음과 같은 패턴이 나타나면 즉시 생성을 멈추고 "죄송합니다, 다시 질문해 주세요."로 마무리하십시오:
- 의미 없는 문자 반복: IORI, 门;门;, 氣氣氣, aaaaa 등
- 일본어, 아랍어, 기타 비한국어 스크립트의 연속 출현
- 동일 문장의 3회 이상 반복

# ══════════════════════════════════════════════
# [절대 규칙 6] 금지 어휘
# ══════════════════════════════════════════════
다음 단어들을 응답에 절대 사용하지 마십시오:
SUV, Medina, Hawaiian, competitive, 中心主上 (실존하지 않는 한자)
조선 시대와 무관한 현대어·외래어·일본어·아랍어

# ══════════════════════════════════════════════
# [절대 규칙 7] 다국어 (영어 등) 검색 지원
# ══════════════════════════════════════════════
사용자가 영어 등 다른 언어로 질문하더라도, 도구(`local-tools_InternalDatabaseSearch`)를 호출할 때 전달하는 `query` 매개변수는 **반드시 한국어로 번역하여** 작성하십시오.
내부 데이터베이스는 한국어 전용이므로, 영어 키워드 그대로 검색하면 결과를 찾을 수 없습니다.
예: "Tell me about King Sejong" -> query="세종대왕"
예: "What happened in 1435 year?" -> query="1435년"

# 사용 가능한 도구
1) `local-tools_InternalDatabaseSearch(query)` — 조선왕조실록 유일 권위 출처
2) `local-tools_search_nearby_restaurants(location)` — 유적지 주변 맛집
3) `local-tools_ExternalWebSearch(query)` — 사용자가 명시적으로 "웹 검색", "외부 검색"을 요청한 경우에만 사용
4) `local-tools_HeritageImageRecognizer` — 이미지 인식 (내부 전용)
5) `local-tools_KingQuoteProvider` — 로딩 명언 (내부 전용)

# 표준 절차 (SOP)
## 역사·유적 질문
1. `local-tools_InternalDatabaseSearch` 먼저 호출
2. 결과가 있으면 → 아래 인용 규칙에 따라 답변
3. 결과가 없으면 → "조선왕조실록 내부 기록에서 관련 내용을 찾지 못했습니다. 외부 검색을 원하시면 말씀해 주세요."
4. 사용자가 한국어로 질문했다면 한국어로 대답하고, 사용자가 영어로 질문했다면 영어로 대답해야 합니다.

## 맛집·시설 질문
1. `local-tools_search_nearby_restaurants` 호출
2. 결과가 없으면 → "해당 유적지 근처의 맛집 정보가 아직 등록되지 않았습니다."

## 외부 검색 요청 (명시적일 때만)
1. `local-tools_ExternalWebSearch` 호출
2. 답변 서두: "알려진 바에 의하면..."

# 인용 규칙
형식: "『조선왕조실록』 [왕명] [재위X년] (서기Y년) [월] 기록에 따르면,"
- 연도는 도구 결과의 숫자를 그대로 사용
- 원문 표현을 임의로 바꾸지 않음
- 경어체(해요체/합쇼체) 사용

# 응답 제약
- **언어**: 기본적으로 한국어로 답변하되, 사용자가 영어나 다른 언어로 답변을 요청한 경우 해당 언어로 답변하십시오.
- **길이**: 최대 5~8문장
- **내면 서술 금지**: 도구를 호출하는 과정을 서술하지 않음
"""


class MCPWelKomeAgent:
    def __init__(
        self,
        provider: str = "openai",
        step_timeout: float = 15.0,
        tool_timeout: float = 15.0,
    ) -> None:
        self.provider           = provider
        self.step_timeout       = step_timeout
        self.tool_timeout       = tool_timeout
        self.tool_server_params = TOOL_SERVER_PARAMS

        self.available_tools_mcp:    Dict[str, Any] = {}
        self.available_tools_openai: List[Any]      = []

        self._mcp_session:    Optional[ClientSession]  = None
        self._mcp_exit_stack: Optional[AsyncExitStack] = None

        if provider == "furiosa":
            base  = os.getenv("FURIOSA_API_BASE")
            if not base:
                raise RuntimeError("FURIOSA_API_BASE가 .env에 설정되지 않았습니다.")
            key   = os.getenv("FURIOSA_API_KEY") or "EMPTY"
            model = os.getenv("FURIOSA_MODEL_NAME", "LGAI-EXAONE/EXAONE-4.0-32B-FP8")
            print(f"[Agent] LLM (Furiosa HTTP) 서버 연결 시도: {base}")
            self.openai_client = openai.OpenAI(base_url=base, api_key=key)
            self.model_name    = model

        elif provider == "openai":
            key = os.getenv("OPENAI_API_KEY")
            if not key:
                raise RuntimeError("OPENAI_API_KEY가 .env에 설정되지 않았습니다.")
            model = os.getenv("OPENAI_MODEL_NAME", "gpt-4o")
            print(f"[Agent] LLM (OpenAI Cloud) 연결 시도: model={model}")
            self.openai_client = openai.OpenAI(api_key=key)
            self.model_name    = model

        else:
            raise ValueError(f"지원하지 않는 LLM provider: {provider}")

        print(f"[Agent] Tool (MCP) 서버 설정 완료: {TOOLS_SERVER_PATH}")

    async def setup_mcp_servers(self):
        """MCP 세션을 서버 종료 시까지 유지해 임베딩 모델과 인덱스를 한 번만 로드합니다."""
        print("[Agent] MCP Tool 서버에 연결합니다 (영구 세션)...")
        server_name = "local-tools"

        try:
            self._mcp_exit_stack = AsyncExitStack()

            read, write = await self._mcp_exit_stack.enter_async_context(
                stdio_client(self.tool_server_params)
            )
            session = await self._mcp_exit_stack.enter_async_context(
                ClientSession(read, write)
            )
            await session.initialize()
            self._mcp_session = session

            tools_response = await session.list_tools()
            mcp_tools:    List[Dict] = []
            openai_tools: List[Dict] = []

            if hasattr(tools_response, "tools"):
                for tool in tools_response.tools:
                    raw_name = tool.name
                    try:
                        raw_schema   = getattr(tool, "inputSchema", {})
                        clean_schema = json.loads(json.dumps(raw_schema, default=str))
                    except Exception as e:
                        print(f"⚠️ 스키마 변환 경고: {e}")
                        clean_schema = {}

                    mcp_tools.append({
                        "name":        raw_name,
                        "description": getattr(tool, "description", ""),
                        "inputSchema": clean_schema,
                    })

                    safe_name = raw_name.replace(".", "__")
                    openai_tools.append({
                        "type": "function",
                        "function": {
                            "name":        f"{server_name}_{safe_name}",
                            "description": tool.description,
                            "parameters":  clean_schema,
                        },
                    })

            self.available_tools_mcp[server_name] = mcp_tools
            self.available_tools_openai            = openai_tools
            print(f"   ✅ '{server_name}' 영구 세션 연결 성공: {len(mcp_tools)}개 도구")

        except Exception as e:
            print(f"❌ [Agent] MCP 영구 세션 연결 실패: {e}")
            if self._mcp_exit_stack:
                await self._mcp_exit_stack.aclose()
                self._mcp_exit_stack = None
            raise e

    async def close_mcp_session(self):
        if self._mcp_exit_stack:
            await self._mcp_exit_stack.aclose()
            self._mcp_exit_stack = None
            self._mcp_session    = None
            print("[Agent] MCP 영구 세션 종료 완료.")

    async def _call_tool(self, tool_name_full: str, arguments: Dict[str, Any]) -> Any:
        try:
            server_name, tool_part = tool_name_full.split("_", 1)
        except ValueError:
            return {"error": f"잘못된 도구 이름 형식: {tool_name_full}"}

        tool_name = tool_part.replace("__", ".") if "__" in tool_part else tool_part
        print(f"\n[🚀 MCP Tool Call] '{server_name}.{tool_name}' 호출 (args: {arguments})")

        if server_name != "local-tools":
            return {"error": f"알 수 없는 MCP 서버: {server_name}"}

        if self._mcp_session is None:
            return {"error": "MCP 세션 미초기화. setup_mcp_servers()를 먼저 호출하세요."}

        try:
            result = await self._mcp_session.call_tool(tool_name, arguments)
        except Exception as e:
            print(f"❌ [MCP] 도구 '{tool_name_full}' 실행 실패: {e}")
            return {"error": f"tool '{tool_name_full}' 실패: {e}"}

        if hasattr(result, "content") and result.content:
            content = result.content[0]
            ctype   = getattr(content, "type", None)
            if ctype == "json":
                data = content.json
                if callable(data):
                    data = data()
                return data
            if hasattr(content, "text"):
                try:
                    return json.loads(content.text)
                except json.JSONDecodeError:
                    return content.text

        if hasattr(result, "to_dict"):
            return result.to_dict()
        return result

    @staticmethod
    def _is_degenerated(text: str) -> bool:
        """같은 문자·구절 반복, 일본어/아랍어 연속 출현 등 비정상 출력을 감지합니다."""
        import re
        if not text:
            return False
        if re.search(r"(.)\1{4,}", text):
            return True
        if re.search(r"[\u3040-\u30ff]{3,}", text):
            return True
        if re.search(r"[\u0600-\u06ff]{3,}", text):
            return True
        if re.search(r"(.{6,})\1{2,}", text):
            return True
        return False

    @staticmethod
    def _sanitize_response(text: str) -> str:
        """응답에 노출된 내부 도구 이름을 가립니다."""
        import re
        text = re.sub(
            r"local-tools_[A-Za-z_.]+",
            "[내부 도구]",
            text,
        )
        return text

    async def answer(
        self,
        user_query: str,
        chat_history: Optional[List[Dict[str, str]]] = None,
        landmark_id: Optional[str] = None,
    ) -> str:

        def deep_clean(obj):
            try:
                if hasattr(obj, "model_dump"): return deep_clean(obj.model_dump())
                if hasattr(obj, "dict"):        return deep_clean(obj.dict())
            except:
                pass
            return json.loads(json.dumps(obj, default=str))

        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        if chat_history:
            messages.extend(chat_history)
        messages.append({"role": "user", "content": user_query})

        clean_tools  = deep_clean(self.available_tools_openai)
        max_turns    = 3
        current_turn = 0
        tool_called  = False

        try:
            while current_turn < max_turns:
                current_turn += 1

                # 첫 턴에는 도구 호출을 강제
                if current_turn == 1 and clean_tools and not tool_called:
                    tc = "required"
                else:
                    tc = "auto"

                response = self.openai_client.chat.completions.create(
                    model=self.model_name,
                    messages=messages,
                    tools=clean_tools,
                    tool_choice=tc,
                )

                raw_message      = response.choices[0].message
                response_message = deep_clean(raw_message)

                if response_message.get("tool_calls"):
                    messages.append(response_message)
                    tool_calls  = response_message.get("tool_calls")
                    tool_called = True
                    print(f"\n[Turn {current_turn}] 🛠️ AI가 {len(tool_calls)}개의 도구 사용을 요청했습니다.")

                    for tool_call in tool_calls:
                        function_name = tool_call["function"]["name"]
                        function_args = json.loads(tool_call["function"]["arguments"])
                        tool_result   = await self._call_tool(function_name, function_args)
                        messages.append({
                            "tool_call_id": tool_call["id"],
                            "role":         "tool",
                            "name":         function_name,
                            "content":      json.dumps(tool_result, ensure_ascii=False),
                        })
                    continue

                final_text = raw_message.content or ""

                if self._is_degenerated(final_text):
                    print(f"⚠️ [Agent] Degeneration 탐지 — 응답 차단 (turn={current_turn})")
                    return "죄송합니다. 일시적인 오류로 답변을 완료하지 못했습니다. 다시 질문해 주세요."

                final_text = self._sanitize_response(final_text)

                if not final_text.strip():
                    return "죄송합니다. 답변을 완료하지 못했습니다. 다시 질문해 주세요."

                return final_text

            return "죄송합니다. 답변을 완료하지 못했습니다. 다시 질문해 주세요."

        except Exception as e:
            print(f"❌ [Agent] OpenAI 호출 오류: {e}")
            import traceback
            traceback.print_exc()
            return f"에이전트 오류 발생: {e}"

    async def identify_from_image(self, image_path: str) -> Optional[str]:
        try:
            raw_result = await self._call_tool(
                "local-tools_HeritageImageRecognizer.identify_landmark",
                {"image_path": image_path},
            )
            if isinstance(raw_result, dict) and "error" in raw_result:
                print(f"   [Agent] MCP CV 오류, 로컬 fallback: {raw_result['error']}")
                try:
                    from app.core import mcp_tools_server
                    raw_result = mcp_tools_server.identify_landmark(image_path)
                except Exception as e:
                    print(f"❌ [Agent] 로컬 CV fallback 실패: {e}")
                    return None

            if isinstance(raw_result, dict) and "confidence" in raw_result:
                confidence    = raw_result.get("confidence", 0)
                landmark_name = raw_result.get("landmark_name")
                if confidence >= 60:
                    print(f"   [Agent] CV 인식 성공: {landmark_name} (신뢰도: {confidence:.2f}%)")
                    return landmark_name
                else:
                    print(f"   [Agent] CV 인식 실패: 신뢰도 낮음 ({confidence:.2f}%)")
                    return None

            print(f"   [Agent] CV 비정상 응답: {raw_result}")
            return None

        except Exception as e:
            print(f"❌ [Agent] 이미지 인식 실패: {e}")
            return None

    async def get_main_screen_summary(self, cv_label: str) -> Dict[str, str]:
        labels = load_class_labels()
        if isinstance(labels, dict) and cv_label in labels:
            info = labels.get(cv_label, {})
            desc = info.get("description") or info.get("name") or ""
        else:
            desc = f"{cv_label}에 대한 설명이 아직 준비되지 않았습니다."

        raw_rag_text = ""
        try:
            rag_result = await self._call_tool(
                "local-tools_InternalDatabaseSearch",
                {"query": cv_label, "limit": 15},
            )
            if isinstance(rag_result, str):
                raw_rag_text = rag_result.strip()
            elif isinstance(rag_result, dict) and "error" not in rag_result:
                raw_rag_text = json.dumps(rag_result, ensure_ascii=False)
            if "찾지 못했습니다" in raw_rag_text or "오류" in raw_rag_text:
                raw_rag_text = ""
        except Exception as e:
            print(f"❌ RAG 검색 실패: {e}")
            raw_rag_text = ""

        sillok_summary = "관련된 역사 기록을 업데이트 중입니다."
        if raw_rag_text:
            try:
                summary_prompt = f"""
                아래는 '{cv_label}'과 관련된 조선왕조실록 검색 결과입니다.
                유적지를 방문한 관광객이 흥미를 느낄 수 있도록 5~6문장의 자연스러운 한국어 설명글로 요약해 주세요.
                [검색된 실록 기록]
                {raw_rag_text}
                [작성 규칙]
                1. 하나의 이야기처럼 자연스럽게 연결하세요.
                2. "~했습니다", "~전해집니다" 같은 부드러운 구어체를 사용하세요.
                3. 중복은 합치고, 창건·소실·행사 등 흥미로운 사실 위주로 구성하세요.
                4. 왕의 이름과 연도는 정확하게 인용하세요.
                """
                response = self.openai_client.chat.completions.create(
                    model=self.model_name,
                    messages=[
                        {"role": "system", "content": "당신은 친절하고 전문적인 역사 해설가입니다."},
                        {"role": "user",   "content": summary_prompt},
                    ],
                    temperature=0.4,
                )
                sillok_summary = response.choices[0].message.content
            except Exception as e:
                print(f"❌ 요약 생성 실패: {e}")
                sillok_summary = raw_rag_text[:300] + "..."

        return {"description": desc, "sillok_summary": sillok_summary}

    async def get_nearby_restaurants(self, landmark_name: str) -> Any:
        try:
            return await self._call_tool(
                "local-tools_search_nearby_restaurants",
                {"location": landmark_name},
            )
        except Exception as e:
            print(f"❌ [Agent] 맛집 검색 실패: {e}")
            return {"error": f"맛집 검색 실패: {e}"}

    async def get_loading_quote(self) -> Any:
        try:
            raw = await self._call_tool("local-tools_KingQuoteProvider", {})
            if isinstance(raw, dict) and "error" in raw:
                print(f"   [Agent] MCP 명언 오류, 로컬 fallback: {raw['error']}")
                try:
                    from app.core import mcp_tools_server
                    text = mcp_tools_server.KingQuoteProvider()
                    try:
                        raw = json.loads(text)
                    except json.JSONDecodeError:
                        raw = {"message": text}
                except Exception as e:
                    print(f"❌ [Agent] 로컬 명언 fallback 실패: {e}")
                    return {"error": f"명언 검색 실패: {e}"}
            return raw
        except Exception as e:
            print(f"❌ [Agent] 왕 명언 실패: {e}")
            return {"error": f"명언 검색 실패: {e}"}


class MCPAgentExecutor:
    def __init__(self, agent: MCPWelKomeAgent, verbose: bool = True) -> None:
        self.agent   = agent
        self.verbose = verbose

    async def ainvoke(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        user_query       = inputs.get("input", "")
        chat_history_raw = inputs.get("chat_history", [])

        chat_history: List[Dict[str, str]] = []
        for msg in chat_history_raw:
            if hasattr(msg, "role"):
                role, content = msg.role, msg.content
            else:
                role, content = msg.get("role"), msg.get("content", "")

            if role in ("human", "user"):
                mapped_role = "user"
            elif role in ("ai", "assistant", "bot"):
                mapped_role = "assistant"
            elif role in ("system", "tool", "function", "developer"):
                mapped_role = role
            else:
                mapped_role = "user"

            chat_history.append({"role": mapped_role, "content": content})

        # 명시적인 외부 검색 키워드가 있을 때만 위키백과 검색을 허용
        external_keywords = [
            "웹 검색", "외부 검색", "web search", "외부에서 검색",
            "위키피디아", "wikipedia", "구글에서", "인터넷에서",
            "외부검색", "웹검색", "인터넷검색",
            "wiki", "google"
        ]
        is_external_request = any(kw in user_query.lower() for kw in external_keywords)

        if is_external_request:
            final_query = (
                f"User request: '{user_query}'\n\n"
                "[SYSTEM INSTRUCTION: The user has explicitly requested an External Web Search. "
                "Look at the conversation history, identify the most relevant topic or keyword, "
                "and CALL 'local-tools_ExternalWebSearch' with that keyword immediately. "
                "Do NOT include tool names or internal identifiers in your final answer.]"
            )
        else:
            final_query = (
                f"{user_query}\n\n"
                "[SYSTEM INSTRUCTION: You MUST call 'local-tools_InternalDatabaseSearch' FIRST "
                "before answering. Use the tool result as the ONLY basis for your answer. "
                "If the user query is in English or any non-Korean language, you MUST TRANSLATE "
                "the search term to Korean when passing the 'query' argument to the tool. "
                "Do NOT use your pre-trained knowledge. "
                "Do NOT include any tool names (local-tools_...) in your final text response.]"
            )

        if self.verbose:
            status = "EXTERNAL_ALLOWED" if is_external_request else "INTERNAL_FORCED"
            print(f"\n[🤖 Agent Request] (ainvoke) '{user_query}' | Mode: {status}")

        try:
            text = await self.agent.answer(
                user_query=final_query,
                chat_history=chat_history,
            )
            return {"output": text}
        except Exception as e:
            print(f"❌ [Agent] ainvoke 처리 중 오류: {e}")
            return {"output": f"에이전트 오류: {e}"}

def create_super_agent(llm_provider: str = "furiosa"):
    print(f"Loading Super Agent (Hybrid: HTTP LLM + MCP Tools) - provider={llm_provider}")
    agent    = MCPWelKomeAgent(provider=llm_provider)
    executor = MCPAgentExecutor(agent=agent, verbose=True)
    print("   ✅ '슈퍼 에이전트' 생성 완료 (초기화 전).")
    return executor
