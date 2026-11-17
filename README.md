# 🛒 쇼핑 리스트

필요한 물건을 적고, 장 보면서 체크하는 간단한 웹 앱입니다. Streamlit으로 만들었습니다.

- 아이템 **추가 / 수정 / 삭제 / 체크**
- 수량, 진행률, 완료 항목 일괄 삭제
- 새로고침해도 목록 유지 (주소의 `?sid=` 값으로 내 목록을 구분)
- 모바일 화면 지원

요구사항은 [`PDR.md`](PDR.md)(PRD)에 있습니다.

## 실행

Python 3.10 이상이 필요합니다.

```bash
pip install -r requirements.txt
streamlit run app.py
```

브라우저에서 http://localhost:8501 로 접속합니다. 처음 접속하면 주소에 `?sid=...`가 붙습니다. **이 주소를 즐겨찾기** 해 두면 같은 목록을 계속 쓸 수 있습니다. `sid` 없는 주소로 접속하면 새 목록이 만들어집니다.

## 데이터 저장

- 목록은 `data/<sid>.json` 파일에 저장됩니다. (`data/`는 Git에서 제외됩니다.)
- 여러 탭이나 기기에서 같은 주소를 열어도 서로의 변경이 덮어써지지 않고 병합됩니다.
- 저장 파일이 손상되면 `data/<sid>.bak`으로 백업하고 새 목록으로 시작합니다.
- 30일 넘게 쓰이지 않은 저장 파일은 서버가 시작될 때 정리됩니다.
- 한도: 이름 50자, 수량 1~99, 아이템 100개.
- 주소(`sid`)를 아는 사람은 그 목록을 볼 수 있습니다. 로그인이 없으므로 민감한 내용은 적지 마세요.

## 배포 (Streamlit Community Cloud)

1. 이 폴더를 GitHub 저장소로 올립니다. (`data/`는 `.gitignore`로 제외됨)
2. https://share.streamlit.io 에서 GitHub 계정을 연결합니다.
3. **New app** → 저장소 / 브랜치 / 메인 파일 `app.py` 선택 → **Deploy**
4. 고급 설정에서 Python 버전을 3.10 이상으로 선택합니다.

> **주의**: Community Cloud는 앱이 재시작되거나 다시 배포되면 서버의 `data/` 파일이 사라질 수 있습니다. 목록을 영구 보관하려면 외부 DB(Supabase, Google Sheets 등)로 저장소를 바꿔야 합니다.

## 개발 참고

- 모바일 레이아웃 CSS는 Streamlit 내부 요소(`data-testid`)에 의존합니다. 그래서 `requirements.txt`에서 Streamlit 버전을 고정했습니다. 버전을 올리면 모바일 화면을 다시 확인하세요.
- `st.session_state`는 `st.session_state["items"]`처럼 대괄호로 접근합니다. `st.session_state.items`는 dict의 `.items()` 메서드와 충돌합니다.
- 사용자 입력은 `safe_text()`를 거쳐서만 마크다운에 넣습니다. `unsafe_allow_html`은 쓰지 않습니다.
