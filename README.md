# ev_card_benefits

카드고릴라(card-gorilla.com) 검색 API에서 "전기차" 키워드로 검색되는 카드 중,
전기차 충전소/충전요금 할인 혜택이 있는 카드를 매일 자동으로 확인합니다.

- 매일 09:00(KST) GitHub Actions가 실행되어 `scripts/fetch_cards.py`로
  최신 카드 목록과 상세 혜택을 가져옵니다.
- 결과는 `data/latest.json`, `data/history/YYYY-MM-DD.json`에 저장되고,
  `docs/index.html`로 정리되어 GitHub Pages에 게시됩니다.
- 전날과 비교해 새로 추가/삭제된 카드, 혜택 내용이 바뀐 카드는
  페이지 상단 "오늘의 변경사항"에 표시됩니다.

## 로컬 실행

```bash
pip install -r requirements.txt
python scripts/fetch_cards.py
```

## 데이터 출처

비공식 공개 API(`api.card-gorilla.com`)를 호출합니다. 카드고릴라 측 정책 변경 시
API 스펙이 바뀌거나 접근이 제한될 수 있습니다.
