# -*- coding: utf-8 -*-
"""홈 화면 「장소 사진 고르기」에 쓸 사진과 장소 정보 미리 받기.

장소마다 위키미디어 공용(Commons)에서 후보 사진을 차례로 받아(가로 1200px JPEG)
실제 서버의 /predict_image 에 보내고, 모델이 알아본 이름(cv_label)이 그 장소와 같으면
사진과 답(description, sillok_summary)을 그대로 저장한다. 다르면 다음 후보로 바꾼다.
/predict_image 는 생성 모델을 부르므로 유료다. 장소당 최대 3회, 전체 최대 24회.

사용:
    python tools/record_places.py --url https://xxxx.ngrok-free.dev
    python tools/record_places.py --url ... --only jongmyo     # 한 장소만 다시 받아 기존 파일에 끼워 넣기
    python tools/record_places.py --offline                     # 서버 없이 사진만 받고 짧은 설명으로 채우기

결과:
    frontend/static/places.json                (기존 파일은 places.backup.json 으로 남긴다)
    frontend/static/images/places/<id>.jpg
    frontend/static/images/places/CREDITS.md
표준 라이브러리만 쓴다.
"""
import argparse
import datetime as dt
import json
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "frontend" / "static"
OUT = STATIC / "places.json"
IMG_DIR = STATIC / "images" / "places"
UA = "welKome-booth-demo/1.0 (educational demo)"
COMMONS = "https://commons.wikimedia.org/w/api.php"
OK_LICENSES = ("cc0", "public domain", "cc by ", "cc by-sa ")   # 비영리(NC)·변경금지(ND)는 받지 않는다
PER_PLACE = 3
TOTAL = 24

# 장소(사진 인식 모델이 아는 이름) · 짧은 설명(모델 class_labels.json 그대로) · Commons 후보 사진(앞에서부터 시도)
PLACES = [
    {"id": "gyeongbokgung", "name": "경복궁",
     "short": "경복궁은 서울특별시 종로구에 위치한 조선의 법궁으로, 1395년에 창건된 이후 조선 왕조를 대표하는 궁궐이자 사적 제117호로 지정된 유산입니다.",
     "candidates": ["File:Gyeongbokgung(palace) Geunjeongjeon(hall).jpg",
                    "File:Geunjeongjeon Seoul.jpg",
                    "File:Front view of the Imperial Throne Hall Geunjeongjeon at Gyeongbokgung Palace with blue sky in Seoul.jpg"]},
    {"id": "changdeokgung", "name": "창덕궁",
     "short": "창덕궁은 서울 종로구에 위치한 조선 왕조의 이궁이자 실질적 법궁으로, 자연 지형을 살린 비정형 배치가 특징인 사적 제122호이자 유네스코 세계문화유산입니다.",
     "candidates": ["File:Injeongjeon Hall 01.jpg", "File:Changdeokgung Seoul 3.jpg",
                    "File:Changdeokgung, Seoul - Changdeokgung3135.jpg"]},   # 인정전 사진은 모델이 대조전으로 봄(2026-11-18)
    {"id": "jongmyo", "name": "종묘",
     "short": "종묘는 서울 종로구 훈정동에 위치한 조선 왕조의 왕과 왕비 신위를 모신 왕실 사당으로, 사적 제125호이자 유네스코 세계문화유산으로 등재된 유산입니다.",
     "candidates": ["File:Jongmyo-Jeongjeon.jpg",
                    "File:Jongmyo Shrine, Seoul IMG 20160922 032431.jpg",
                    "File:Jeongjeon, Jongmyo Shrine (oblique) - Seoul, Korea.jpg"]},
    {"id": "changgyeonggung", "name": "창경궁",
     "short": "창경궁은 서울 종로구 와룡동에 위치한 조선 시대 궁궐로, 왕과 왕족의 생활 공간 역할을 했으며 사적 제123호로 지정된 서울의 5대 궁궐 중 하나입니다.",
     "candidates": ["File:Changgyeonggung 20191221 06.jpg", "File:Myeongjeongjeon(명정전).jpg",
                    "File:Glass house (exterior), Changgyeonggung - Seoul, Korea.jpg"]},   # 명정전 사진은 인식 실패 · 경복궁(2026-11-18)
    {"id": "heunginjimun", "name": "흥인지문",
     "short": "흥인지문은 서울 종로구 종로6가에 위치한 한양도성의 동쪽 대문으로, 흔히 동대문이라 불리며 조선 후기 형식을 보여주는 성곽문이자 보물 제1호입니다.",
     "candidates": ["File:20240601 144028 Heunginjimun, Seoul 07.jpg", "File:Korea-Seoul-Dongdaemun gate.jpg"]},
    {"id": "donhwamun", "name": "돈화문",
     "short": "돈화문은 서울 종로구 창덕궁의 정문으로, 조선 중기에 세워진 중층 누각 형식의 궁문이자 현존 궁궐 정문 중 가장 오래된 건물로 보물 제383호로 지정되어 있습니다.",
     "candidates": ["File:Donhwamun Gate Changdeokgung 2.jpg", "File:Donhwamun Gate Changdeokgung 1.jpg"]},
    # 광성보는 뺐다: 실록 요약이 장소와 무관한 '광성' 기록을 엮음(2026-11-18 점검)
    {"id": "sajikdan", "name": "사직단",
     "short": "사직단은 서울 종로구 사직로 일대에 위치한 제단으로, 조선 시대에 토지신(사)과 곡식신(직)에게 제사를 올리던 국가 제사 공간이자 사적 제121호로 지정된 유산입니다.",
     "candidates": ["File:20140805 사직단 전경.jpg",
                    "File:Sajikdan Shrine in Seoul, Korea 02.jpg",
                    "File:Gate of Sajikdan Shrine in Seoul, Korea 02.jpg"]},
]


def get(url, headers=None, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def strip_tags(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s or "")).strip()


def commons_photo(title, width=1200):
    """Commons 파일 하나의 라이선스를 확인하고 가로 width px JPEG 와 출처를 돌려준다."""
    q = urllib.parse.urlencode({"action": "query", "format": "json", "titles": title, "prop": "imageinfo",
                                "iiprop": "url|size|mime|extmetadata", "iiurlwidth": width})
    page = next(iter(json.loads(get(f"{COMMONS}?{q}"))["query"]["pages"].values()))
    info = page["imageinfo"][0]
    meta = info.get("extmetadata", {})
    lic = strip_tags(meta.get("LicenseShortName", {}).get("value"))
    low = lic.lower() + " "
    if not low.startswith(OK_LICENSES) or "-nc" in low or "-nd" in low:
        raise ValueError(f"쓸 수 없는 라이선스: {lic}")
    if info.get("mime") != "image/jpeg":
        raise ValueError(f"JPEG 아님: {info.get('mime')}")
    data = get(info["thumburl"], timeout=120)
    credit = {"file": page["title"], "page": info["descriptionurl"],
              "author": strip_tags(meta.get("Artist", {}).get("value")) or "작자 미상",
              "license": lic, "license_url": meta.get("LicenseUrl", {}).get("value", ""),
              "width": info.get("thumbwidth"), "height": info.get("thumbheight")}
    return data, credit


def predict(url, jpeg, filename, timeout):
    """사진 한 장을 /predict_image 에 보낸다(multipart, 필드 이름 file)."""
    boundary = uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
            f"Content-Type: image/jpeg\r\n\r\n").encode() + jpeg + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(url.rstrip("/") + "/predict_image", data=body, method="POST", headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}", "ngrok-skip-browser-warning": "1", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def server_alive(url):
    try:
        data = json.loads(get(url.rstrip("/") + "/loading_quote", {"ngrok-skip-browser-warning": "1"}, timeout=40))
        return bool(data.get("agent_response"))
    except Exception as e:  # ERR_NGROK_3200(터널 꺼짐) 등
        print(f"서버 확인 실패: {e}", flush=True)
        return False


def load_short_labels(path):
    if not path:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {k: v.get("description", "") for k, v in data.items()}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:8001")
    ap.add_argument("--only", help="이 장소 id 만 다시 받기(예: jongmyo)")
    ap.add_argument("--skip", type=int, default=0, help="--only 와 함께: 앞의 후보 사진 이만큼 건너뛰기(이미 시도한 사진)")
    ap.add_argument("--per-place", type=int, default=PER_PLACE, help=f"장소당 최대 호출(기본 {PER_PLACE})")
    ap.add_argument("--offline", action="store_true", help="서버를 부르지 않고 사진과 짧은 설명만")
    ap.add_argument("--labels", help="모델의 class_labels.json (짧은 설명을 여기서 채움)")
    ap.add_argument("--timeout", type=int, default=300)
    args = ap.parse_args()

    shorts = load_short_labels(args.labels)
    for p in PLACES:
        p["short"] = shorts.get(p["name"]) or p["short"] or ""

    old = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else None
    targets = [p for p in PLACES if not args.only or p["id"] == args.only]
    if not targets:
        sys.exit(f"그런 장소 id 가 없습니다: {args.only}")

    online = not args.offline and server_alive(args.url)
    if not args.offline and not online:
        print("서버가 꺼져 있어 장소 정보 받기는 건너뜁니다(recorded=false).", flush=True)

    IMG_DIR.mkdir(parents=True, exist_ok=True)
    calls, results, log = 0, {}, []
    for p in targets:
        used, chosen = 0, None
        for title in p["candidates"][args.skip if args.only else 0:]:
            if used >= args.per_place or calls >= TOTAL:
                break
            try:
                jpeg, credit = commons_photo(title)
            except Exception as e:
                print(f"[{p['name']}] 사진 건너뜀 {title}: {e}", flush=True)
                continue
            if not online:
                chosen = {"credit": credit, "jpeg": jpeg, "answer": None}
                break
            answer = None
            for attempt in (1, 2):   # 실패하면 한 번만 다시
                if used >= args.per_place or calls >= TOTAL:
                    break
                used += 1
                calls += 1
                try:
                    t0 = time.time()
                    answer = predict(args.url, jpeg, f"{p['id']}.jpg", args.timeout)
                    print(f"[{p['name']}] {title} → cv_label={answer.get('cv_label')} ({time.time() - t0:.1f}초)", flush=True)
                    break
                except urllib.error.HTTPError as e:
                    detail = e.read().decode("utf-8", "replace")[:200]
                    print(f"[{p['name']}] 실패({attempt}) HTTP {e.code}: {detail}", flush=True)
                    if e.code == 404:   # 모델이 알아보지 못함 → 다시 보내지 않고 다음 사진
                        break
                except Exception as e:
                    print(f"[{p['name']}] 실패({attempt}): {e}", flush=True)
                time.sleep(3)
            log.append({"place": p["name"], "file": title, "cv_label": (answer or {}).get("cv_label")})
            if answer and answer.get("cv_label") == p["name"]:
                chosen = {"credit": credit, "jpeg": jpeg, "answer": answer}
                break
        if not chosen:
            print(f"[{p['name']}] 맞는 사진을 찾지 못해 이 장소를 뺍니다.", flush=True)
            continue
        results[p["id"]] = chosen

    if not results:
        sys.exit("저장할 장소가 없습니다. 기존 파일은 바꾸지 않았습니다.")

    # 사진 저장
    for pid, c in results.items():
        target = IMG_DIR / f"{pid}.jpg"
        if target.exists():
            shutil.copy2(target, target.with_suffix(".backup.jpg"))
        target.write_bytes(c["jpeg"])

    now = dt.datetime.now().isoformat(timespec="seconds")
    old_places = {q["id"]: q for q in (old or {}).get("places", [])} if args.only else {}
    places = []
    for p in PLACES:
        if p["id"] in results:
            c = results[p["id"]]
            a = c["answer"]
            places.append({
                "id": p["id"], "name": p["name"], "image": f"/static/images/places/{p['id']}.jpg",
                "short": p["short"], "recorded": a is not None, "recorded_at": now if a else None,
                "cv_label": a.get("cv_label") if a else None,
                "description": a.get("description", "") if a else "",
                "sillok_summary": a.get("sillok_summary", "") if a else "",
                "credit": {k: c["credit"][k] for k in ("file", "page", "author", "license", "license_url")},
            })
        elif p["id"] in old_places:
            places.append(old_places[p["id"]])
    if args.only and old:   # 앞선 실행의 호출 횟수와 기록을 이어 붙인다
        calls_all, log_all = old.get("calls", 0) + calls, old.get("log", []) + log
    else:
        calls_all, log_all = calls, log
    data = {"recorded": any(q.get("recorded") for q in places), "updated_at": now,
            "server": args.url if online else None, "calls": calls_all, "log": log_all, "places": places}

    if OUT.exists():
        shutil.copy2(OUT, OUT.with_name("places.backup.json"))
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(OUT)

    lines = ["# 장소 사진 출처", "",
             "홈 화면 「장소 사진 고르기」의 사진. 모두 위키미디어 공용(Wikimedia Commons)에서 받았고, 가로 1200px로 줄인 것 말고는 바꾸지 않았다.", "",
             "| 장소 | 파일 | 작가 | 라이선스 |", "|---|---|---|---|"]
    for q in places:
        c = q["credit"]
        lic = f"[{c['license']}]({c['license_url']})" if c.get("license_url") else c["license"]
        lines.append(f"| {q['name']} | [{c['file']}]({c['page']}) | {c['author']} | {lic} |")
    (IMG_DIR / "CREDITS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"서버 호출 {calls}회 · 저장 {len(places)}곳: {OUT}", flush=True)


if __name__ == "__main__":
    main()
