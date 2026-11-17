# -*- coding: utf-8 -*-
"""시연 탭에 쓸 실제 기록 만들기.

실제 서버(bash run_demo.sh)가 켜져 있는 컴퓨터에서 한 번 실행한다.
질문마다 /chat/stream 을 불러 진행 이벤트와 최종 답변을 그대로 저장한다.
질문 하나에 생성 모델 호출이 두 번 안팎 생기므로(유료) 10문항이면 20회 안팎이다.

사용:
    python tools/record_demo.py                       # 기본: http://127.0.0.1:8001, 질문 10개
    python tools/record_demo.py --url http://127.0.0.1:8000
    python tools/record_demo.py --only 3              # 3번 질문만 다시 기록해 기존 파일에 끼워 넣기

결과: frontend/static/demo_runs.json (기존 파일은 demo_runs.backup.json 으로 남긴다)
표준 라이브러리만 쓴다.
"""
import argparse
import datetime as dt
import json
import shutil
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "frontend" / "static" / "demo_runs.json"

QUESTIONS = [
    "세종 때 훈민정음은 언제 만들어졌어?",
    "경복궁은 언제 처음 지어졌어?",
    "세종대왕의 건강은 어땠어?",
    "1452년에는 무슨 일이 있었어?",
    "장영실은 어떤 일을 했어?",
    "조선시대에 전염병이 퍼지면 어떻게 했어?",
    "측우기는 언제 만들어졌어?",
    "정조는 수원 화성을 왜 지었어?",
    "태종은 어떤 왕이었어?",
    "조선시대 과거 시험은 어떻게 봤어?",
]


def ask(url, question, timeout):
    """질문 하나를 보내고 (진행 이벤트 목록, 결과 이벤트)를 돌려준다."""
    body = json.dumps({"input": question, "chat_history": []}).encode("utf-8")
    req = urllib.request.Request(url.rstrip("/") + "/chat/stream", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    events, result = [], None
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        buf = []
        for raw in resp:
            line = raw.decode("utf-8").rstrip("\r\n")
            if line:
                buf.append(line)
                continue
            payload = "\n".join(l[5:].lstrip() for l in buf if l.startswith("data:"))
            buf = []
            if not payload:
                continue
            event = json.loads(payload)
            kind = event.pop("type", None)
            if kind == "progress":
                events.append(event)
                print(f"    {event.get('elapsed_ms', 0) / 1000:6.2f}s  {event.get('title', '')}", flush=True)
            elif kind == "error":
                raise RuntimeError(event.get("message", "서버 오류"))
            elif kind == "result":
                result = event
                break
    if result is None:
        raise RuntimeError("결과 없이 연결이 끝났습니다")
    return events, result


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:8001")
    ap.add_argument("--only", type=int, help="이 번호(1부터) 질문만 다시 기록")
    ap.add_argument("--timeout", type=int, default=300)
    args = ap.parse_args()

    targets = [args.only] if args.only else list(range(1, len(QUESTIONS) + 1))
    old = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else None
    if args.only and (not old or old.get("sample")):
        sys.exit("--only 는 실제 기록 파일이 이미 있을 때만 쓸 수 있습니다.")
    runs = list(old["runs"]) if args.only else [None] * len(QUESTIONS)

    for n in targets:
        q = QUESTIONS[n - 1]
        print(f"[{n}/{len(QUESTIONS)}] {q}", flush=True)
        for attempt in (1, 2):
            try:
                t0 = time.time()
                events, result = ask(args.url, q, args.timeout)
                break
            except Exception as e:  # 한 번만 다시 시도
                print(f"    실패({attempt}): {e}", flush=True)
                if attempt == 2:
                    sys.exit("기록을 멈춥니다. 기존 파일은 바꾸지 않았습니다.")
                time.sleep(3)
        runs[n - 1] = {"question": q, "events": events, "output": result.get("output", ""),
                       "elapsed_ms": result.get("elapsed_ms")}
        print(f"    완료 {time.time() - t0:.1f}초 · 이벤트 {len(events)}개 · 답변 {len(runs[n - 1]['output'])}자", flush=True)

    data = {"sample": False, "recorded_at": dt.datetime.now().isoformat(timespec="seconds"),
            "server": args.url, "runs": runs}
    if OUT.exists():
        shutil.copy2(OUT, OUT.with_name("demo_runs.backup.json"))
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(OUT)
    print(f"저장: {OUT}")


if __name__ == "__main__":
    main()
