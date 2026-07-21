# ev_card_benefits

카드고릴라(card-gorilla.com) 검색 API에서 "전기차" 키워드로 검색되는 카드 중,
전기차 충전소/충전요금 할인 혜택이 있는 카드를 매월 자동으로 확인합니다.

- 매월 1일 09:00(KST) GitHub Actions가 실행되어 `scripts/fetch_cards.py`로
  최신 카드 목록과 상세 혜택을 가져옵니다.
- 결과는 `data/latest.json`, `data/history/YYYY-MM-DD.json`에 저장되고,
  `docs/index.html`로 정리되어 GitHub Pages에 게시됩니다.
- 지난 실행과 비교해 새로 추가/삭제된 카드, 혜택 내용이 바뀐 카드는
  페이지 상단 "오늘의 변경사항"에 표시됩니다.
- `fetch_cards.py`의 정규식 필터는 오탐이 많아(예: "LPG충전소 제외" 문구도 매칭),
  이어서 `scripts/classify_benefits.py`가 LLM으로 각 혜택 문구를 다시 읽어
  실제 전기차 충전 혜택만 골라 구조화한 `data/ev_only.json`을 생성합니다.
  이미 분류한 문구는 `data/classify_cache.json`에 캐시해 재호출하지 않습니다.
- 비용 절감을 위해 캐시가 비어있는 최초 실행(기준 분류)만 `claude-opus-4-8`을 쓰고,
  이후 신규/변경분만 처리하는 실행은 `claude-haiku-4-5`로 자동 전환됩니다.

## 로컬 실행

```bash
pip install -r requirements.txt
python scripts/fetch_cards.py
export ANTHROPIC_API_KEY=sk-...   # console.anthropic.com에서 발급
python scripts/classify_benefits.py
```

GitHub Actions에서 돌리려면 저장소 Settings → Secrets and variables → Actions에
`ANTHROPIC_API_KEY`를 등록해야 합니다.

## 데이터 출처

비공식 공개 API(`api.card-gorilla.com`)를 호출합니다. 카드고릴라 측 정책 변경 시
API 스펙이 바뀌거나 접근이 제한될 수 있습니다.
