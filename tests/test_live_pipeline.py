"""Offline checks: no model downloads, paid API calls, or production index writes.

Run from the demo worktree: python -m unittest discover -s tests -v
"""
import asyncio
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from langchain_core.documents import Document
from mcp.shared.memory import create_connected_server_and_client_session
from openai.types.chat import ChatCompletionMessage
from app.core import mcp_tools_server as search
from app.core.agent import MCPWelKomeAgent
from app import main

DOCS = [
    Document(page_content="검증용 기록: 훈민정음 창제와 경복궁 기록", metadata={"king_name": "세종", "king_order": 4, "year_solar": 1446, "month": "9", "month_int": 9, "year_reign": "28년"}),
    Document(page_content="검증용 기록: 세종의 건강과 의약", metadata={"king_name": "세종", "king_order": 4, "year_solar": 1445, "month": "3", "month_int": 3, "year_reign": "27년"}),
    Document(page_content="검증용 기록: 궁궐 보수", metadata={"king_name": "태종", "king_order": 3, "year_solar": 1410, "month": "1", "month_int": 1, "year_reign": "10년"}),
]

class Vector:
    def similarity_search(self, *args, **kwargs): return list(DOCS)
    def max_marginal_relevance_search(self, *args, **kwargs): return list(DOCS)

def fixture():
    return patch.multiple(search,
        load_rag_db=lambda: None, load_bm25_index=lambda: None,
        RAG_DB=Vector(), BM25_PAYLOAD={"chunks": [d.model_dump() for d in DOCS],
            "bm25": SimpleNamespace(get_scores=lambda tokens: [2.0, 1.0, 0.0])},
        _TOKENIZE_FN=lambda text: text.split(), _TOKENIZER_NAME="검증용 토크나이저")

class SearchChecks(unittest.IsolatedAsyncioTestCase):
    async def test_real_mcp_progress_and_routes(self):
        with fixture():
            async with create_connected_server_and_client_session(search.app) as session:
                tools = await session.list_tools()
                schema = next(t.inputSchema for t in tools.tools if t.name == 'InternalDatabaseSearch')
                self.assertNotIn('ctx', schema['properties'])
                for query, route in [('세종', 'B'), ('세종 건강', 'A'), ('훈민정음', 'C')]:
                    events = []
                    async def callback(n, total, message):
                        events.append((n, json.loads(message)))
                    result = await session.call_tool('InternalDatabaseSearch', {'query': query, 'limit': 2}, progress_callback=callback)
                    self.assertFalse(result.isError)
                    self.assertEqual([n for n, _ in events], list(range(1, len(events) + 1)))
                    observations = [e for _, e in events]
                    self.assertEqual(next(e['route'] for e in observations if e['stage'] == 'route'), route)
                    self.assertEqual(observations[-1]['stage'], 'evidence')
                    self.assertEqual(len(observations[-1]['documents']), 2)
                    if route == 'B':
                        self.assertFalse(any(e['stage'] in ('tokenize', 'bm25', 'fusion') for e in observations))
                    if route == 'C':
                        self.assertTrue(any(e['stage'] == 'tokenize' for e in observations))
                        self.assertTrue(any(e['stage'] == 'fusion' for e in observations))

    async def test_parallel_requests_do_not_mix(self):
        with fixture():
            async with create_connected_server_and_client_session(search.app) as session:
                async def call(query):
                    events = []
                    async def callback(n, total, message): events.append(json.loads(message))
                    result = await session.call_tool('InternalDatabaseSearch', {'query': query}, progress_callback=callback)
                    self.assertFalse(result.isError)
                    return [e['query'] for e in events if 'query' in e]
                first, second = await asyncio.gather(call('세종'), call('훈민정음'))
                self.assertEqual(set(first), {'세종'})
                self.assertEqual(set(second), {'훈민정음'})

    async def test_missing_bm25_and_missing_database(self):
        with fixture(), patch.object(search, 'BM25_PAYLOAD', None):
            events = []
            token = search._TRACE.set(events.append)
            try: text = search._database_search('훈민정음')
            finally: search._TRACE.reset(token)
            self.assertIn('세종', text)
            self.assertTrue(any(e['stage'] == 'bm25' and e['status'] == 'warning' for e in events))
            self.assertFalse(any(e['stage'] in ('fusion', 'tokenize') for e in events))
        with fixture(), patch.object(search, 'RAG_DB', None):
            events = []
            token = search._TRACE.set(events.append)
            try: search._database_search('세종')
            finally: search._TRACE.reset(token)
            self.assertEqual(events[-1]['status'], 'error')

    async def test_empty_results_fallback_and_temporal_order(self):
        with fixture():
            events = []
            token = search._TRACE.set(events.append)
            try:
                with patch.object(search, '_chunks_scan', return_value=[]):
                    search._database_search('세종')
                text = search._database_search('세종 최초', limit=2)
            finally: search._TRACE.reset(token)
            self.assertTrue(any(e['stage'] == 'fallback' for e in events))
            self.assertTrue(any(e['stage'] == 'sort' for e in events))
            self.assertLess(text.index('1445'), text.index('1446'))

    async def test_mcp_database_error_stops_stream_agent(self):
        with fixture(), patch.object(search, 'RAG_DB', None):
            async with create_connected_server_and_client_session(search.app) as session:
                agent = MCPWelKomeAgent.__new__(MCPWelKomeAgent)
                agent._mcp_session = session
                events = []
                async def callback(event): events.append(event)
                with self.assertRaises(RuntimeError):
                    await agent._call_tool('local-tools_InternalDatabaseSearch', {'query': '세종'}, progress=callback)
                self.assertEqual(events[-1]['status'], 'error')
                # The original non-stream API keeps its existing string result.
                text = await agent._call_tool('local-tools_InternalDatabaseSearch', {'query': '세종'})
                self.assertIn('초기화되지', text)

class StreamChecks(unittest.IsolatedAsyncioTestCase):
    async def test_first_event_arrives_before_answer_and_disconnect_cancels(self):
        finished = asyncio.Event()
        cancelled = asyncio.Event()
        async def invoke(inputs):
            await inputs['progress']({'stage': 'model', 'status': 'running', 'title': 'test'})
            try: await finished.wait()
            finally: cancelled.set()
            return {'output': '답변'}
        with patch.dict(main.APP_STATE, agent=SimpleNamespace(ainvoke=invoke)):
            response = await main.stream_chat(main.ChatRequest(input='세종'))
            first = await anext(response.body_iterator)
            self.assertIn('질문 접수', first)
            self.assertFalse(finished.is_set())
            await response.body_iterator.aclose()
            self.assertTrue(cancelled.is_set())

    async def test_success_history_and_error_terminal(self):
        async def invoke(inputs):
            await inputs['progress']({'stage': 'model', 'status': 'done', 'title': 'test'})
            return {'output': '검증용 답변'}
        request = main.ChatRequest(input='질문', chat_history=[{'role': 'ai', 'content': '인사'}])
        with patch.dict(main.APP_STATE, agent=SimpleNamespace(ainvoke=invoke)):
            response = await main.stream_chat(request)
            events = [json.loads(frame.removeprefix('data: ')) async for frame in response.body_iterator]
            self.assertEqual(events[-1]['type'], 'result')
            self.assertEqual(len(events[-1]['chat_history']), 3)
            self.assertEqual(events[-1]['chat_history'][-2]['content'], '질문')
        async def broken(inputs):
            await inputs['progress']({'stage': 'database', 'status': 'error', 'title': 'missing'})
            return {'output': '실패를 성공으로 처리하면 안 됨'}
        with patch.dict(main.APP_STATE, agent=SimpleNamespace(ainvoke=broken)):
            response = await main.stream_chat(request)
            events = [json.loads(frame.removeprefix('data: ')) async for frame in response.body_iterator]
            self.assertEqual(events[-1]['type'], 'error')
            self.assertFalse(any(e['type'] == 'result' for e in events))

    async def test_agent_model_call_does_not_block_event_loop(self):
        import time
        agent = MCPWelKomeAgent.__new__(MCPWelKomeAgent)
        agent.available_tools_openai = []
        agent.model_name = 'offline'
        def create(**kwargs):
            time.sleep(.12)
            return SimpleNamespace(choices=[SimpleNamespace(message=ChatCompletionMessage(role='assistant', content='검증용 답변'))])
        agent.openai_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        agent._is_degenerated = lambda text: False
        agent._sanitize_response = lambda text: text
        events = []
        async def callback(e): events.append(e)
        task = asyncio.create_task(agent.answer('질문', progress=callback))
        await asyncio.sleep(.03)
        self.assertFalse(task.done())
        self.assertEqual(events[0]['status'], 'running')
        self.assertEqual(await task, '검증용 답변')

if __name__ == '__main__': unittest.main()
