"""카드고릴라에서 '전기차' 검색 결과 카드들을 가져와
충전소/충전요금 관련 혜택만 추려서 data/, docs/index.html 에 기록한다.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
HISTORY_DIR = DATA_DIR / "history"
DOCS_DIR = ROOT / "docs"
LATEST_PATH = DATA_DIR / "latest.json"

SEARCH_URL = "https://api.card-gorilla.com:8080/v1/cards/search/fast"
DETAIL_URL = "https://api.card-gorilla.com:8080/v1/cards/{idx}"
KEYWORD = "전기차"
CHARGE_PATTERN = re.compile("충전소|충전요금|전기차\\s*충전")
RATE_PATTERN = re.compile(r"\d+(?:\.\d+)?%")
BENEFIT_TYPES = ["캐시백", "청구할인", "결제일할인", "포인트 적립", "적립", "할인"]
KST = timezone(timedelta(hours=9))


def strip_html(raw: str) -> str:
    text = re.sub(r"<[^>]+>", " ", raw or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def fetch_search_results(keyword: str) -> list[dict]:
    resp = requests.get(
        SEARCH_URL, params={"p": 1, "perPage": 50, "keyword": keyword}, timeout=20
    )
    resp.raise_for_status()
    payload = resp.json()
    return payload["data"]


def fetch_card_detail(idx: int) -> dict:
    resp = requests.get(DETAIL_URL.format(idx=idx), timeout=20)
    resp.raise_for_status()
    return resp.json()


def parse_benefit_summary(comment: str) -> dict:
    rates = RATE_PATTERN.findall(comment)
    benefit_type = next((t for t in BENEFIT_TYPES if t in comment), None)
    return {"rates": rates, "type": benefit_type}


def extract_charge_benefits(detail: dict) -> list[dict]:
    found = []
    for kb in detail.get("key_benefit") or []:
        title = kb.get("title") or ""
        cate_name = (kb.get("cate") or {}).get("name") or ""
        comment = kb.get("comment") or ""
        info_text = strip_html(kb.get("info") or "")
        haystack = " ".join([title, cate_name, comment, info_text])
        if CHARGE_PATTERN.search(haystack):
            found.append(
                {
                    "title": title,
                    "comment": comment,
                    "detail": info_text,
                    **parse_benefit_summary(comment),
                }
            )
    return found


def build_snapshot() -> dict:
    results = fetch_search_results(KEYWORD)
    cards = {}
    for item in results:
        idx = item["idx"]
        try:
            detail = fetch_card_detail(idx)
        except requests.RequestException as exc:
            print(f"[warn] failed to fetch card {idx}: {exc}", file=sys.stderr)
            continue
        charge_benefits = extract_charge_benefits(detail)
        cards[str(idx)] = {
            "idx": idx,
            "name": detail.get("name") or item.get("name"),
            "corp": (detail.get("corp") or {}).get("name") or item.get("corp_txt"),
            "cate": item.get("cate_txt"),
            "card_img": (item.get("card_img") or {}).get("url"),
            "detail_url": f"https://www.card-gorilla.com/card/detail/{idx}",
            "has_charge_benefit": bool(charge_benefits),
            "charge_benefits": charge_benefits,
        }
    return {
        "keyword": KEYWORD,
        "fetched_at": datetime.now(KST).isoformat(),
        "total_searched": len(results),
        "total_with_charge_benefit": sum(
            1 for c in cards.values() if c["has_charge_benefit"]
        ),
        "cards": cards,
    }


def diff_snapshots(prev: dict | None, curr: dict) -> dict:
    prev_cards = (prev or {}).get("cards", {})
    curr_cards = curr["cards"]
    prev_ids, curr_ids = set(prev_cards), set(curr_cards)

    added = sorted(curr_ids - prev_ids)
    removed = sorted(prev_ids - curr_ids)
    changed = []
    for idx in sorted(curr_ids & prev_ids):
        if prev_cards[idx].get("charge_benefits") != curr_cards[idx].get(
            "charge_benefits"
        ):
            changed.append(idx)

    return {
        "added": [curr_cards[i] for i in added],
        "removed": [prev_cards[i] for i in removed],
        "changed": [
            {"before": prev_cards[i], "after": curr_cards[i]} for i in changed
        ],
    }


def render_html(curr: dict, diff: dict) -> str:
    def card_row(card: dict) -> str:
        benefits_html = "".join(
            f"<li><strong>{b['title']}</strong> — {b['comment']}<br>"
            f"<span class='detail'>{b['detail']}</span></li>"
            for b in card["charge_benefits"]
        )
        return f"""
        <div class="card">
          <img src="{card['card_img'] or ''}" alt="{card['name']}" />
          <div class="card-body">
            <h3><a href="{card['detail_url']}" target="_blank">{card['name']}</a></h3>
            <p class="corp">{card['corp']}</p>
            <ul>{benefits_html}</ul>
          </div>
        </div>"""

    charge_cards = [
        c for c in curr["cards"].values() if c["has_charge_benefit"]
    ]
    charge_cards.sort(key=lambda c: c["name"] or "")

    change_notice = ""
    if diff["added"] or diff["removed"] or diff["changed"]:
        parts = []
        if diff["added"]:
            names = ", ".join(c["name"] for c in diff["added"])
            parts.append(f"<li>신규: {names}</li>")
        if diff["removed"]:
            names = ", ".join(c["name"] for c in diff["removed"])
            parts.append(f"<li>검색결과에서 사라짐: {names}</li>")
        if diff["changed"]:
            names = ", ".join(c["after"]["name"] for c in diff["changed"])
            parts.append(f"<li>혜택 내용 변경: {names}</li>")
        change_notice = f"<section class='changes'><h2>오늘의 변경사항</h2><ul>{''.join(parts)}</ul></section>"
    else:
        change_notice = "<section class='changes'><h2>오늘의 변경사항</h2><p>변경 없음</p></section>"

    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>전기차 충전 카드 혜택 트래커</title>
<style>
  body {{ font-family: -apple-system, sans-serif; max-width: 900px; margin: 0 auto; padding: 24px; color: #222; }}
  h1 {{ font-size: 1.4rem; }}
  .updated {{ color: #666; font-size: 0.85rem; margin-bottom: 24px; }}
  .changes {{ background: #fffbe6; border: 1px solid #f0e0a0; border-radius: 8px; padding: 12px 16px; margin-bottom: 24px; }}
  .card {{ display: flex; gap: 16px; border: 1px solid #ddd; border-radius: 8px; padding: 16px; margin-bottom: 16px; }}
  .card img {{ width: 80px; height: auto; object-fit: contain; }}
  .card-body h3 {{ margin: 0 0 4px; font-size: 1.05rem; }}
  .corp {{ color: #888; font-size: 0.85rem; margin: 0 0 8px; }}
  .card ul {{ padding-left: 18px; margin: 0; }}
  .detail {{ color: #555; font-size: 0.85rem; }}
  @media (prefers-color-scheme: dark) {{
    body {{ background: #1a1a1a; color: #eee; }}
    .card {{ border-color: #444; }}
    .changes {{ background: #332e10; border-color: #665c1f; }}
    .corp, .detail {{ color: #aaa; }}
  }}
</style>
</head>
<body>
<h1>전기차 충전 카드 혜택 트래커</h1>
<p class="updated">키워드 "{curr['keyword']}" 검색 결과 {curr['total_searched']}건 중 충전 혜택 카드 {curr['total_with_charge_benefit']}건 · 마지막 업데이트: {curr['fetched_at']}</p>
{change_notice}
<section>
{''.join(card_row(c) for c in charge_cards)}
</section>
</body>
</html>"""


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    prev = json.loads(LATEST_PATH.read_text()) if LATEST_PATH.exists() else None
    curr = build_snapshot()
    diff = diff_snapshots(prev, curr)

    snapshot_json = json.dumps(curr, ensure_ascii=False, indent=2)
    LATEST_PATH.write_text(snapshot_json)
    today = datetime.now(KST).strftime("%Y-%m-%d")
    (HISTORY_DIR / f"{today}.json").write_text(snapshot_json)
    (DOCS_DIR / "index.html").write_text(render_html(curr, diff))
    (DOCS_DIR / "latest.json").write_text(snapshot_json)

    print(
        f"cards={curr['total_searched']} "
        f"with_charge_benefit={curr['total_with_charge_benefit']} "
        f"added={len(diff['added'])} removed={len(diff['removed'])} changed={len(diff['changed'])}"
    )


if __name__ == "__main__":
    main()
