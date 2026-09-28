"""Qwen 일정 처리 100건 자동 테스트 및 6개 항목 자동 채점."""

import csv
import json
import urllib.request

MODEL = "qwen3:4b"
BASE_DATE = "2026-09-25"
OUTPUT_FILE = "qwen_100_scored_results.csv"
SUMMARY_FILE = "qwen_100_scored_summary.csv"


def t(sentence, intent, date=None, time=None, location=None, category=None, content=None):
    return (sentence, intent, date, time, location, category, content)


# 기준 날짜는 2026-09-25이다. content에는 category 단어를 중복하지 않는다.
TESTS = [
    # 등록 25건
    t("오늘 오후 3시에 B구역 반품 확인 등록해줘", "create", "2026-09-25", "15:00", "B구역", "반품", "확인"),
    t("내일 오전 9시에 A구역 재고 조사 넣어줘", "create", "2026-09-26", "09:00", "A구역", "재고", "조사"),
    t("모레 오후 2시에 C구역 입고 정리 추가해줘", "create", "2026-09-27", "14:00", "C구역", "입고", "정리"),
    t("오늘 오후 5시 B구역 피킹 검수 일정 잡아줘", "create", "2026-09-25", "17:00", "B구역", "피킹", "검수"),
    t("내일 오전 10시에 A구역 출고 확인 등록해", "create", "2026-09-26", "10:00", "A구역", "출고", "확인"),
    t("오늘 오후 3시 30분에 C구역 반품 분류 넣어줘", "create", "2026-09-25", "15:30", "C구역", "반품", "분류"),
    t("내일 오후 1시에 B구역 재고 확인 일정 추가해줘", "create", "2026-09-26", "13:00", "B구역", "재고", "확인"),
    t("다음 주 월요일 오전 8시에 A구역 입고 물품 검수 등록해줘", "create", "2026-09-28", "08:00", "A구역", "입고", "물품 검수"),
    t("오늘 오후 4시에 C구역 피킹 작업 넣어줘", "create", "2026-09-25", "16:00", "C구역", "피킹", "작업"),
    t("내일 오후 6시에 B구역 출고 준비 추가해줘", "create", "2026-09-26", "18:00", "B구역", "출고", "준비"),
    t("오늘 오후 12시 30분에 A구역 반품 정리 등록해줘", "create", "2026-09-25", "12:30", "A구역", "반품", "정리"),
    t("모레 오전 7시에 C구역 재고 실사 일정 넣어줘", "create", "2026-09-27", "07:00", "C구역", "재고", "실사"),
    t("다음 주 화요일 오후 3시에 B구역 입고 검수 잡아줘", "create", "2026-09-29", "15:00", "B구역", "입고", "검수"),
    t("오늘 오후 2시 15분에 A구역 출고 포장 추가해줘", "create", "2026-09-25", "14:15", "A구역", "출고", "포장"),
    t("내일 오전 11시에 C구역 피킹 목록 확인 등록해줘", "create", "2026-09-26", "11:00", "C구역", "피킹", "목록 확인"),
    t("오늘 오후 4시에 B구역 반품 검수 하나 추가해", "create", "2026-09-25", "16:00", "B구역", "반품", "검수"),
    t("내일 오전 10시 반에 A구역 재고 정리 넣어줘", "create", "2026-09-26", "10:30", "A구역", "재고", "정리"),
    t("C구역 입고 물품 확인을 오늘 오후 4시에 등록해줘", "create", "2026-09-25", "16:00", "C구역", "입고", "물품 확인"),
    t("오늘 오후 세 시에 A구역 출고 검수 추가해줘", "create", "2026-09-25", "15:00", "A구역", "출고", "검수"),
    t("내일 오후 두 시에 B구역 피킹 포장 넣어줘", "create", "2026-09-26", "14:00", "B구역", "피킹", "포장"),
    t("오늘 오전 8시에 C구역 반품 사진 촬영 일정 잡아줘", "create", "2026-09-25", "08:00", "C구역", "반품", "사진 촬영"),
    t("내일 오후 4시에 A구역 재고 위치 확인 등록해줘", "create", "2026-09-26", "16:00", "A구역", "재고", "위치 확인"),
    t("모레 오전 9시에 B구역 입고 수량 확인 추가해줘", "create", "2026-09-27", "09:00", "B구역", "입고", "수량 확인"),
    t("오늘 오후 6시에 C구역 피킹 마감 확인 넣어줘", "create", "2026-09-25", "18:00", "C구역", "피킹", "마감 확인"),
    t("내일 오전 7시 45분에 A구역 출고 상차 확인 등록해줘", "create", "2026-09-26", "07:45", "A구역", "출고", "상차 확인"),
    # 조회 25건
    t("오늘 일정 보여줘", "read", "2026-09-25", content="일정 조회"),
    t("내일 일정 뭐 있어", "read", "2026-09-26", content="일정 조회"),
    t("B구역 오늘 일정 알려줘", "read", "2026-09-25", location="B구역", content="일정 조회"),
    t("오늘 반품 일정만 보여줘", "read", "2026-09-25", category="반품", content="일정 조회"),
    t("내일 오전 일정 조회해줘", "read", "2026-09-26", content="일정 조회"),
    t("A구역 재고 관련 일정 확인해줘", "read", location="A구역", category="재고", content="일정 조회"),
    t("모레 출고 일정 알려줘", "read", "2026-09-27", category="출고", content="일정 조회"),
    t("다음 주 월요일 일정 보여줘", "read", "2026-09-28", content="일정 조회"),
    t("C구역 일정 전부 보여줘", "read", location="C구역", content="일정 조회"),
    t("오늘 반품 확인 몇 시에 있어", "read", "2026-09-25", category="반품", content="확인"),
    t("내일 오전 9시 일정이 있는지 확인해줘", "read", "2026-09-26", "09:00", content="일정 조회"),
    t("B구역 피킹 작업 일정 찾아줘", "read", location="B구역", category="피킹", content="작업"),
    t("오늘 할 일 뭐야", "read", "2026-09-25", content="일정 조회"),
    t("입고 일정 다 보여줘", "read", category="입고", content="일정 조회"),
    t("내일 C구역에 뭐 해야 해", "read", "2026-09-26", location="C구역", content="일정 조회"),
    t("반품 일정 조회", "read", category="반품", content="일정 조회"),
    t("오늘 오전에 등록된 일정 보여줘", "read", "2026-09-25", content="일정 조회"),
    t("A구역 일정 중에 재고 확인 찾아줘", "read", location="A구역", category="재고", content="확인"),
    t("오늘 오후 3시 이후 일정 보여줘", "read", "2026-09-25", "15:00", content="일정 조회"),
    t("내일 오후 5시 A구역 출고 일정 있어", "read", "2026-09-26", "17:00", "A구역", "출고", "일정 조회"),
    t("오늘 C구역 피킹 검수 일정 보여줘", "read", "2026-09-25", location="C구역", category="피킹", content="검수"),
    t("모레 오전 7시 재고 일정 확인해줘", "read", "2026-09-27", "07:00", category="재고", content="일정 조회"),
    t("다음 주 화요일 입고 일정 찾아줘", "read", "2026-09-29", category="입고", content="일정 조회"),
    t("오늘 오후 6시 출고 마감 일정 알려줘", "read", "2026-09-25", "18:00", category="출고", content="마감"),
    t("B구역 반품 검수 일정 조회해줘", "read", location="B구역", category="반품", content="검수"),
    # 수정 25건
    t("오늘 오후 3시 B구역 반품 확인을 오후 4시로 바꿔줘", "update", "2026-09-25", "16:00", "B구역", "반품", "확인"),
    t("내일 오전 9시 C구역 입고 정리를 오전 10시로 변경해줘", "update", "2026-09-26", "10:00", "C구역", "입고", "정리"),
    t("오늘 A구역 재고 확인을 B구역으로 옮겨줘", "update", "2026-09-25", location="B구역", category="재고", content="확인"),
    t("내일 오후 5시 출고 확인 시간을 오후 6시로 미뤄줘", "update", "2026-09-26", "18:00", category="출고", content="확인"),
    t("오늘 C구역 피킹 검수를 내일로 변경해줘", "update", "2026-09-26", location="C구역", category="피킹", content="검수"),
    t("B구역 반품 분류 일정을 오후 2시로 바꿔줘", "update", time="14:00", location="B구역", category="반품", content="분류"),
    t("내일 오전 10시 A구역 입고 검수를 오후 1시로 수정해줘", "update", "2026-09-26", "13:00", "A구역", "입고", "검수"),
    t("오늘 오후 4시 피킹 작업 장소를 C구역으로 바꿔줘", "update", "2026-09-25", "16:00", "C구역", "피킹", "작업"),
    t("오늘 B구역 출고 준비를 출고 검수로 바꿔줘", "update", "2026-09-25", location="B구역", category="출고", content="검수"),
    t("내일 오후 3시 반품 확인을 오후 4시 30분으로 바꿔줘", "update", "2026-09-26", "16:30", category="반품", content="확인"),
    t("A구역 입고 정리를 오전 9시로 앞당겨줘", "update", time="09:00", location="A구역", category="입고", content="정리"),
    t("오늘 오후 2시 재고 확인을 내일로 옮겨줘", "update", "2026-09-26", "14:00", category="재고", content="확인"),
    t("내일 C구역 피킹 작업을 B구역으로 변경해", "update", "2026-09-26", location="B구역", category="피킹", content="작업"),
    t("오늘 오후 5시 출고 포장을 오후 5시 30분으로 미뤄줘", "update", "2026-09-25", "17:30", category="출고", content="포장"),
    t("오늘 A구역 재고 실사 시간을 오후 1시로 수정해줘", "update", "2026-09-25", "13:00", "A구역", "재고", "실사"),
    t("내일 입고 물품 확인 일정을 오전 11시로 변경해줘", "update", "2026-09-26", "11:00", category="입고", content="물품 확인"),
    t("B구역 일정 중 피킹 검수 시간을 오후 4시로 바꿔줘", "update", time="16:00", location="B구역", category="피킹", content="검수"),
    t("오늘 오후 3시 C구역 반품 확인 내용을 사진 촬영으로 바꿔줘", "update", "2026-09-25", "15:00", "C구역", "반품", "사진 촬영"),
    t("내일 오전 8시 입고 일정을 오후 1시로 바꿔줘", "update", "2026-09-26", "13:00", category="입고", content="일정"),
    t("출고 확인 일정 장소 A구역으로 수정해줘", "update", location="A구역", category="출고", content="확인"),
    t("오늘 재고 확인을 오후 4시로 변경해", "update", "2026-09-25", "16:00", category="재고", content="확인"),
    t("오늘 오후 6시 B구역 출고 검수를 내일 오전 9시로 바꿔줘", "update", "2026-09-26", "09:00", "B구역", "출고", "검수"),
    t("모레 C구역 반품 정리를 다음 주 월요일 오후 2시로 변경해줘", "update", "2026-09-28", "14:00", "C구역", "반품", "정리"),
    t("내일 오전 7시 A구역 출고 상차를 오전 8시로 수정해줘", "update", "2026-09-26", "08:00", "A구역", "출고", "상차"),
    t("오늘 오후 1시 B구역 피킹 포장을 오후 2시로 바꿔줘", "update", "2026-09-25", "14:00", "B구역", "피킹", "포장"),
    # 삭제 25건
    t("오늘 오후 3시 B구역 반품 확인 일정 삭제해줘", "delete", "2026-09-25", "15:00", "B구역", "반품", "확인"),
    t("내일 오전 9시 C구역 입고 정리 취소해줘", "delete", "2026-09-26", "09:00", "C구역", "입고", "정리"),
    t("오늘 A구역 재고 확인 지워줘", "delete", "2026-09-25", location="A구역", category="재고", content="확인"),
    t("내일 오후 5시 출고 확인 일정 없애줘", "delete", "2026-09-26", "17:00", category="출고", content="확인"),
    t("B구역 피킹 검수 삭제해줘", "delete", location="B구역", category="피킹", content="검수"),
    t("오늘 C구역 반품 분류 일정 취소해", "delete", "2026-09-25", location="C구역", category="반품", content="분류"),
    t("모레 오전 10시 A구역 입고 검수 지워줘", "delete", "2026-09-27", "10:00", "A구역", "입고", "검수"),
    t("다음 주 월요일 재고 조사 일정 취소해줘", "delete", "2026-09-28", category="재고", content="조사"),
    t("오늘 오후 4시 피킹 작업 삭제해줘", "delete", "2026-09-25", "16:00", category="피킹", content="작업"),
    t("내일 B구역 출고 준비 없애줘", "delete", "2026-09-26", location="B구역", category="출고", content="준비"),
    t("오늘 오후 2시 30분 반품 확인 일정 취소해줘", "delete", "2026-09-25", "14:30", category="반품", content="확인"),
    t("A구역 재고 실사 일정 지워", "delete", location="A구역", category="재고", content="실사"),
    t("오늘 오후 3시 B구역 입고 물품 확인 삭제해줘", "delete", "2026-09-25", "15:00", "B구역", "입고", "물품 확인"),
    t("내일 오전 8시 C구역 피킹 목록 확인 취소해줘", "delete", "2026-09-26", "08:00", "C구역", "피킹", "목록 확인"),
    t("오늘 오후 6시 A구역 출고 마감 확인 없애줘", "delete", "2026-09-25", "18:00", "A구역", "출고", "마감 확인"),
    t("모레 B구역 반품 사진 촬영 일정 지워줘", "delete", "2026-09-27", location="B구역", category="반품", content="사진 촬영"),
    t("내일 오후 4시 재고 위치 확인 취소해줘", "delete", "2026-09-26", "16:00", category="재고", content="위치 확인"),
    t("오늘 오전 9시 C구역 입고 수량 확인 삭제해줘", "delete", "2026-09-25", "09:00", "C구역", "입고", "수량 확인"),
    t("내일 오후 2시 A구역 피킹 포장 일정 없애줘", "delete", "2026-09-26", "14:00", "A구역", "피킹", "포장"),
    t("오늘 오전 7시 B구역 출고 상차 확인 지워줘", "delete", "2026-09-25", "07:00", "B구역", "출고", "상차 확인"),
    # 문맥·대상 모호 사례 5건
    t("방금 등록한 일정 취소해", "delete"),
    t("그거 삭제해", "delete"),
    t("아까 넣은 피킹 일정 없애줘", "delete", category="피킹"),
    t("오후 3시 일정 삭제해", "delete", time="15:00"),
    t("재고 확인 일정 하나 지워줘", "delete", category="재고", content="확인"),
]


def make_prompt(sentence):
    return f"""너는 창고 업무 일정 처리 도우미다.
기준 날짜는 {BASE_DATE}이다.
사용자 문장을 아래 JSON 형식으로만 변환해라. 설명이나 코드블록은 쓰지 마라.
{{
  "intent": "create | read | update | delete",
  "date": "YYYY-MM-DD 또는 null",
  "time": "HH:MM 또는 null",
  "location": "장소 또는 null",
  "category": "업무 분류 또는 null",
  "content": "업무 내용 또는 null"
}}
category는 반품, 재고, 입고, 피킹, 출고 중 하나로 작성한다.
category에 이미 들어간 단어는 content에 중복하지 마라.
문장에 없는 정보는 실제 JSON null로 작성한다. 문자열 "null"을 쓰지 마라.
사용자 문장: {sentence}"""


def normalize(value):
    if value is None:
        return None
    if isinstance(value, str) and value.strip().lower() in ("", "null", "none"):
        return None
    return value.strip() if isinstance(value, str) else value


def ask_qwen(sentence):
    payload = json.dumps({
        "model": MODEL, "prompt": make_prompt(sentence), "format": "json",
        "think": False, "stream": False,
    }).encode("utf-8")
    request = urllib.request.Request("http://localhost:11434/api/generate", data=payload,
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=300) as response:
        body = json.loads(response.read().decode("utf-8"))
    raw = body.get("response", "").strip()
    if not raw:
        raise ValueError("Qwen이 빈 답을 반환했습니다.")
    return raw, json.loads(raw)


def main():
    fields = ["intent", "date", "time", "location", "category", "content"]
    rows = []
    for number, test in enumerate(TESTS, start=1):
        sentence, *expected_values = test
        expected = dict(zip(fields, expected_values))
        print(f"[{number}/{len(TESTS)}] 테스트 중: {sentence}")
        try:
            raw, result = ask_qwen(sentence)
            actual = {field: normalize(result.get(field)) for field in fields}
            correct = sum(actual[field] == expected[field] for field in fields)
            judgment = "정답" if correct == 6 else "부분 정답" if correct else "오답"
        except Exception:
            raw, actual, correct, judgment = "", {field: None for field in fields}, 0, "실행 실패"

        rows.append({
            "번호": number, "사용자 발화": sentence,
            "정답 intent": expected["intent"], "정답 date": expected["date"],
            "정답 time": expected["time"], "정답 location": expected["location"],
            "정답 category": expected["category"], "정답 content": expected["content"],
            "Qwen 결과 원문": raw, "Qwen intent": actual["intent"],
            "Qwen date": actual["date"], "Qwen time": actual["time"],
            "Qwen location": actual["location"], "Qwen category": actual["category"],
            "Qwen content": actual["content"], "자동 판정": judgment,
            "일치 항목 수": f"{correct}/6",
        })

    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    total = len(rows)
    summary = [
        ("총 테스트 수", total),
        ("정상 실행 수", sum(row["자동 판정"] != "실행 실패" for row in rows)),
        ("완전 정답 수 (6/6)", sum(row["자동 판정"] == "정답" for row in rows)),
        ("부분 정답 수 (1~5/6)", sum(row["자동 판정"] == "부분 정답" for row in rows)),
        ("오답 수 (0/6)", sum(row["자동 판정"] == "오답" for row in rows)),
        ("실행 실패 수", sum(row["자동 판정"] == "실행 실패" for row in rows)),
    ]
    for field in fields:
        summary.append((f"{field} 일치 수", sum(
            normalize(row[f"Qwen {field}"]) == normalize(row[f"정답 {field}"])
            for row in rows if row["자동 판정"] != "실행 실패"
        )))
    with open(SUMMARY_FILE, "w", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        writer.writerow(["지표", "값"])
        writer.writerows(summary)

    print(f"완료: {OUTPUT_FILE}")
    print(f"요약: {SUMMARY_FILE}")


if __name__ == "__main__":
    main()
