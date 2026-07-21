"""fetch_cards.py가 만든 data/latest.json 은 "충전소|충전요금|전기차 충전" 정규식으로
1차 후보만 골라둔 상태라 오탐이 많다 (LPG충전소 제외 문구, 다른 카테고리에 복붙된
참조표, "전기차 충전은 적립 제외" 같은 배제 문구 등).

이 스크립트는 각 charge_benefits 항목의 detail 전문을 LLM에 보여주고
1) 실제로 전기차 충전소 할인/적립 혜택이 맞는지
2) 맞다면 할인유형/구간(전월실적별 요율·한도)/대상 충전사업자/다른 카테고리와
   공유하는 한도인지
를 구조화해서 뽑아낸다. 이미 분류한 적 있는 문구(해시로 판별)는 다시 LLM을
호출하지 않고 캐시에서 재사용한다.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

from anthropic import Anthropic

ROOT = Path(__file__).resolve().parent.parent
LATEST_PATH = ROOT / "data" / "latest.json"
OUT_PATH = ROOT / "data" / "ev_only.json"
CACHE_PATH = ROOT / "data" / "classify_cache.json"

MODEL_BOOTSTRAP = "claude-opus-4-8"  # 캐시가 비어있는 최초 실행: 기준이 되는 분류라 고품질 모델 사용
MODEL_INCREMENTAL = "claude-haiku-4-5"  # 이후 신규/변경분만 처리하는 실행: 저비용 모델
BATCH_SIZE = 20

SYSTEM_PROMPT = """당신은 한국 신용/체크카드 약관 문구에서 "전기차 충전소/충전요금 할인" 혜택만
정확히 골라내는 추출기입니다.

주의해야 할 함정 (실제로 발생했던 오탐 사례):
1. "전기차 충전은 포인트 적립 제외" 처럼 전기차가 배제 대상으로만 언급된 경우는
   전기차 혜택이 아닙니다 (is_ev_related=false).
2. "LPG충전소 제외", "주유소, 충전소, 정비소 등은 제외" 처럼 "충전소"라는 단어가
   나와도 전기차와 무관하게 LPG/가스 충전소를 가리키는 경우가 많습니다. 리터(L)
   단위로 계산되는 항목은 대부분 주유/LPG이지 전기차가 아닙니다.
3. 카드사가 여러 카테고리의 적립률을 요약한 표(예: "이동통신, 대중교통, 전기차충전 5%")를
   서로 다른 카테고리 설명에 반복 복붙해두는 경우가 있습니다. 그 항목 자체의 주제가
   전기차 충전이 아니라면(예: 커피, 영화, 쇼핑 항목에 그 표가 끼어있는 경우) 전기차
   혜택이 아닙니다.
4. 하나의 항목이 대중교통/택시/주유/렌터카 등과 전기차 충전을 함께 묶어 동일한 할인율과
   동일한 월 한도를 공유하는 경우가 흔합니다. 이때는 전기차 부분만 추출하되
   shared_categories에 함께 묶인 다른 카테고리를 적어서, 한도가 전기차 전용이 아님을
   표시하세요.
5. 수소차 충전과 전기차 충전이 같은 요율로 묶여 있으면 그대로 추출하고
   operators나 conditions_note에 수소차도 포함된다는 사실을 남기세요.

각 입력 항목(id, title, comment, detail)에 대해 다음 스키마를 가진 JSON 객체를
정확히 하나씩, 입력과 같은 순서의 JSON 배열로만 응답하세요. 설명, 코드블록 표시,
그 외 텍스트를 절대 추가하지 마세요.

{
  "id": "<입력의 id 그대로>",
  "is_ev_related": true|false,
  "discount_type": "percent"|"flat_amount"|"cashback_percent"|"point_percent"|"per_unit"|null,
  "tiers": [ { "min_monthly_spend": <숫자|null>, "rate": <숫자|null>, "cap_amount": <숫자|null> } ],
  "operators": ["<정제된 충전 사업자/브랜드명>", ...],
  "shared_categories": ["<함께 같은 한도를 공유하는 다른 카테고리명>", ...],
  "conditions_note": "<멤버십 등록 필요 여부, 오프라인/앱 결제 조건, 관리비 통합청구 제외 등 핵심 조건을 한두 문장으로>",
  "needs_review": true|false
}

is_ev_related=false 인 경우 discount_type, tiers, operators는 빈 값([], null)으로,
conditions_note는 왜 제외했는지 짧게 남기세요. 판단이 애매하면 needs_review=true로
표시하고 최선의 추정치를 채우세요."""


def entry_hash(title: str, comment: str, detail: str) -> str:
    raw = f"{title}\x1f{comment}\x1f{detail}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def load_cache() -> dict:
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text())
    return {}


def save_cache(cache: dict) -> None:
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2))


def parse_llm_json(text: str) -> list[dict]:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[len("json") :]
    return json.loads(text.strip())


def classify_batch(client: Anthropic, items: list[dict], model: str) -> list[dict]:
    user_content = (
        "다음 항목들을 스키마에 맞춰 분류/추출하세요:\n\n"
        + json.dumps(items, ensure_ascii=False, indent=2)
    )
    resp = client.messages.create(
        model=model,
        max_tokens=8192,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    )
    text = "".join(
        block.text for block in resp.content if getattr(block, "type", None) == "text"
    )
    results = parse_llm_json(text)
    by_id = {r["id"]: r for r in results}
    missing = [it["id"] for it in items if it["id"] not in by_id]
    if missing:
        print(f"[warn] LLM 응답 누락: {missing}", file=sys.stderr)
    return results


def collect_entries(latest: dict) -> list[dict]:
    entries = []
    for idx, card in latest["cards"].items():
        for i, benefit in enumerate(card.get("charge_benefits") or []):
            entries.append(
                {
                    "card_idx": idx,
                    "entry_index": i,
                    "title": benefit.get("title") or "",
                    "comment": benefit.get("comment") or "",
                    "detail": benefit.get("detail") or "",
                }
            )
    return entries


def main() -> None:
    if not LATEST_PATH.exists():
        print(f"[error] {LATEST_PATH} 없음. 먼저 fetch_cards.py를 실행하세요.", file=sys.stderr)
        sys.exit(1)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("[error] ANTHROPIC_API_KEY 환경변수가 설정되어 있지 않습니다.", file=sys.stderr)
        sys.exit(1)

    latest = json.loads(LATEST_PATH.read_text())
    cache = load_cache()
    entries = collect_entries(latest)

    to_process = []
    for e in entries:
        h = entry_hash(e["title"], e["comment"], e["detail"])
        e["hash"] = h
        if h not in cache:
            to_process.append(e)

    is_bootstrap = not cache
    model = MODEL_BOOTSTRAP if is_bootstrap else MODEL_INCREMENTAL
    print(f"entries={len(entries)} cached={len(entries) - len(to_process)} new={len(to_process)} model={model}")

    if to_process:
        client = Anthropic(api_key=api_key)
        for i in range(0, len(to_process), BATCH_SIZE):
            batch = to_process[i : i + BATCH_SIZE]
            llm_items = [
                {
                    "id": f"{e['card_idx']}:{e['entry_index']}",
                    "title": e["title"],
                    "comment": e["comment"],
                    "detail": e["detail"],
                }
                for e in batch
            ]
            results = classify_batch(client, llm_items, model)
            by_id = {r["id"]: r for r in results}
            for e in batch:
                key = f"{e['card_idx']}:{e['entry_index']}"
                result = by_id.get(key)
                if result is None:
                    result = {
                        "id": key,
                        "is_ev_related": False,
                        "discount_type": None,
                        "tiers": [],
                        "operators": [],
                        "shared_categories": [],
                        "conditions_note": "LLM 응답 누락으로 검토 필요",
                        "needs_review": True,
                    }
                cache[e["hash"]] = result

        save_cache(cache)

    ev_cards = {}
    review_needed = []
    for idx, card in latest["cards"].items():
        ev_benefits = []
        for i, benefit in enumerate(card.get("charge_benefits") or []):
            h = entry_hash(
                benefit.get("title") or "",
                benefit.get("comment") or "",
                benefit.get("detail") or "",
            )
            result = cache.get(h)
            if not result or not result.get("is_ev_related"):
                continue
            entry = {
                "source_title": benefit.get("title") or "",
                "discount_type": result.get("discount_type"),
                "tiers": result.get("tiers") or [],
                "operators": result.get("operators") or [],
                "shared_categories": result.get("shared_categories") or [],
                "conditions_note": result.get("conditions_note") or "",
                "needs_review": bool(result.get("needs_review")),
            }
            ev_benefits.append(entry)
            if entry["needs_review"]:
                review_needed.append({"card_idx": idx, "name": card["name"], **entry})

        if ev_benefits:
            ev_cards[idx] = {
                "idx": card["idx"],
                "name": card["name"],
                "corp": card["corp"],
                "cate": card["cate"],
                "card_img": card["card_img"],
                "detail_url": card["detail_url"],
                "ev_benefits": ev_benefits,
            }

    out = {
        "generated_at": latest["fetched_at"],
        "source_total_searched": latest["total_searched"],
        "total_ev_cards": len(ev_cards),
        "review_needed_count": len(review_needed),
        "review_needed": review_needed,
        "cards": ev_cards,
    }
    OUT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"ev_cards={len(ev_cards)} review_needed={len(review_needed)} -> {OUT_PATH}")


if __name__ == "__main__":
    main()
