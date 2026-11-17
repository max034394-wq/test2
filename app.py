from __future__ import annotations

import copy
import json
import os
import re
import shutil
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import streamlit as st

st.set_page_config(page_title="쇼핑 리스트", page_icon="🛒")

MAX_NAME_LEN = 50
MIN_QTY, MAX_QTY = 1, 99
MAX_ITEMS = 100  # PRD 성능 기준(100개 1초 이내)에 맞춘 상한
MAX_FILE_BYTES = 256 * 1024
STALE_DAYS = 30  # 이 기간 동안 쓰이지 않은 저장 파일은 정리한다
DATA_DIR = Path(__file__).parent / "data"
SID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
ZWSP = "​"  # 보이지 않는 문자. 마크다운 자동 링크 변환을 끊는 데 쓴다.


# ---------- 입력 정리 ----------

def clean_name(name: str | None) -> tuple[str, bool]:
    """공백을 제거하고 최대 길이로 자른다. (이름, 잘렸는지)를 돌려준다."""
    name = (name or "").strip()
    return name[:MAX_NAME_LEN].strip(), len(name) > MAX_NAME_LEN


def clean_qty(qty) -> tuple[int, bool]:
    """수량을 1~99로 맞춘다. (수량, 범위를 벗어나 조정했는지)를 돌려준다."""
    try:
        value = int(qty)
    except (TypeError, ValueError):
        return MIN_QTY, True
    clamped = min(max(value, MIN_QTY), MAX_QTY)
    return clamped, clamped != value


# ---------- 상태 변경 로직 (UI와 분리) ----------

def make_item(name: str, quantity: int = 1) -> dict:
    return {"id": str(uuid.uuid4()), "name": name, "quantity": quantity, "checked": False}


def find_item(item_id: str | None) -> dict | None:
    return next((i for i in st.session_state["items"] if i["id"] == item_id), None)


def add_item(name: str, quantity: int) -> None:
    st.session_state["items"].append(make_item(name, quantity))


def update_item(item_id: str, name: str, quantity: int) -> None:
    item = find_item(item_id)
    if item is not None:
        item["name"] = name
        item["quantity"] = quantity


def delete_item(item_id: str) -> None:
    st.session_state["items"] = [i for i in st.session_state["items"] if i["id"] != item_id]
    if st.session_state["editing_id"] == item_id:
        st.session_state["editing_id"] = None


def toggle_item(item_id: str, checked: bool) -> None:
    item = find_item(item_id)
    if item is not None:
        item["checked"] = checked


def clear_checked() -> None:
    st.session_state["items"] = [i for i in st.session_state["items"] if not i["checked"]]
    if find_item(st.session_state["editing_id"]) is None:
        st.session_state["editing_id"] = None


# ---------- 저장 (새로고침 유지 / 다중 탭 병합) ----------
# 브라우저 URL의 ?sid=<uuid hex> 로 사용자를 구분하고, 목록은 data/<sid>.json 에 저장한다.
# - 저장할 때 파일을 다시 읽어 "내가 바꾼 부분만" 병합한다 (다른 탭의 변경을 덮어쓰지 않는다).
# - 읽기에 실패하면 덮어쓰지 않는다. 손상된 파일은 .bak 으로 백업한다.

def get_sid() -> str:
    sid = st.query_params.get("sid", "")
    if not SID_PATTERN.match(sid):  # 없거나 형식이 틀리면(경로 조작 방지) 새로 발급
        sid = uuid.uuid4().hex
        st.query_params["sid"] = sid
    return sid


def data_path() -> Path:
    return DATA_DIR / f"{st.session_state['sid']}.json"


def parse_items(raw) -> tuple[list[dict], int]:
    """항목 단위로 검증한다. 잘못된 항목만 건너뛰고 (항목들, 건너뛴 수)를 돌려준다."""
    if not isinstance(raw, list):
        raise ValueError("목록 형식이 아님")
    items, seen, dropped = [], set(), 0
    for r in raw:
        try:
            if not isinstance(r["checked"], bool):
                raise ValueError
            name, _ = clean_name(str(r["name"]))
            item = {
                "id": str(r["id"]),
                "name": name,
                "quantity": clean_qty(r["quantity"])[0],
                "checked": r["checked"],
            }
            if not name or item["id"] in seen:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            dropped += 1
            continue
        seen.add(item["id"])
        items.append(item)
    return items, dropped


def backup_data() -> bool:
    try:
        shutil.copy2(data_path(), data_path().with_suffix(".bak"))
        return True
    except OSError:
        return False


def write_disk(items: list[dict]) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    tmp = DATA_DIR / f"{st.session_state['sid']}.{uuid.uuid4().hex[:8]}.tmp"
    try:
        tmp.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
        for attempt in range(3):  # 다른 프로세스가 파일을 읽는 중이면 잠깐 기다렸다가 재시도
            try:
                tmp.replace(data_path())  # 원자적 교체
                return
            except PermissionError:
                if attempt == 2:
                    raise
                time.sleep(0.05)
    finally:
        tmp.unlink(missing_ok=True)


def read_disk() -> tuple[list[dict] | None, str | None]:
    """(항목들, 문제) 를 돌려준다. 항목이 None이면 일시적 읽기 실패라서 덮어쓰면 안 된다."""
    path = data_path()
    try:
        if not path.exists():
            return [], None
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("파일이 너무 큼")
        items, dropped = parse_items(json.loads(path.read_text(encoding="utf-8")))
    except ValueError:  # JSON 오류, 인코딩 오류, 형식 오류
        if backup_data():
            path.unlink(missing_ok=True)  # 다음 실행에서 같은 경고가 반복되지 않게 치운다
            return [], "corrupt"
        return None, None  # 백업에 실패하면 원본을 건드리지 않는다
    except OSError:
        return None, None
    if dropped:
        if not backup_data():
            return items, None
        write_disk(items)
        return items, "partial"
    return items, None


@contextmanager
def file_lock(timeout: float = 2.0):
    """같은 sid 파일을 동시에 쓰지 않도록 하는 간단한 잠금 파일."""
    DATA_DIR.mkdir(exist_ok=True)
    lock = data_path().with_suffix(".lock")
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > 10:  # 비정상 종료로 남은 잠금
                    lock.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            if time.monotonic() > deadline:
                raise TimeoutError("잠금을 얻지 못함")  # OSError의 하위 클래스: 저장은 다음 실행에서 재시도
            time.sleep(0.02)
    try:
        yield
    finally:
        os.close(fd)
        lock.unlink(missing_ok=True)


def merge(base: list[dict], mine: list[dict], disk: list[dict]) -> list[dict]:
    """base→mine 에서 내가 바꾼 것(추가/삭제/수정)만 disk 의 최신 상태에 적용한다."""
    base_by = {i["id"]: i for i in base}
    removed = set(base_by) - {i["id"] for i in mine}
    out = [dict(i) for i in disk if i["id"] not in removed]
    index = {i["id"]: n for n, i in enumerate(out)}
    for item in mine:
        iid = item["id"]
        if iid not in base_by:  # 내가 추가한 항목
            if iid not in index:
                out.append(dict(item))
                index[iid] = len(out) - 1
        elif item != base_by[iid] and iid in index:  # 내가 수정한 항목: 바꾼 필드만 덮어쓴다
            for key in ("name", "quantity", "checked"):
                if item[key] != base_by[iid][key]:
                    out[index[iid]][key] = item[key]
    return out


def sync_items() -> None:
    """콜백이 끝난 시점의 내 변경을 파일에 병합하고, 다른 탭의 변경도 가져온다."""
    mine, base = st.session_state["items"], st.session_state["_base"]
    dirty = mine != base
    try:
        if dirty:
            with file_lock():
                disk, problem = read_disk()
                if disk is None:
                    return  # 읽기 실패: 덮어쓰지 않고 다음 실행에서 재시도
                merged = merge(base, mine, disk)
                if merged != disk:
                    write_disk(merged)
        else:
            merged, problem = read_disk()
            if merged is None:
                return
    except OSError:
        return  # 저장 실패: 변경 사항은 메모리에 남아 있고 다음 실행에서 재시도
    if problem == "corrupt":
        flash("warning", "저장된 목록을 읽을 수 없어 백업해 두고 새 목록으로 시작했어요.")
    elif problem == "partial":
        flash("warning", "일부 항목을 읽을 수 없어 제외했어요. 원본은 백업해 두었어요.")
    st.session_state["items"] = merged
    st.session_state["_base"] = copy.deepcopy(merged)
    if find_item(st.session_state["editing_id"]) is None:  # 다른 탭에서 지워진 항목을 수정 중이던 경우
        st.session_state["editing_id"] = None


@st.cache_resource
def cleanup_old_files() -> int:
    """서버가 시작된 뒤 한 번, 오래 쓰이지 않은 저장 파일과 남은 임시 파일을 지운다."""
    removed = 0
    if not DATA_DIR.exists():
        return removed
    now = time.time()
    for path in DATA_DIR.iterdir():
        limit = 3600 if path.suffix in (".tmp", ".lock") else STALE_DAYS * 86400
        try:
            if path.is_file() and now - path.stat().st_mtime > limit:
                path.unlink()
                removed += 1
        except OSError:
            pass
    return removed


def init_state() -> None:
    # 주의: st.session_state.items 는 dict.items() 메서드와 충돌하므로 항상 대괄호로 접근한다.
    if "sid" not in st.session_state:
        st.session_state["sid"] = get_sid()
        try:
            os.utime(data_path())  # 사용 중인 파일이 정리 대상이 되지 않도록 갱신
        except OSError:
            pass
    if "items" not in st.session_state:
        st.session_state["items"] = []
        st.session_state["_base"] = []  # 마지막으로 파일과 동기화한 상태 (sync_items 가 채운다)
    if "editing_id" not in st.session_state:
        st.session_state["editing_id"] = None


# ---------- 콜백 ----------

def flash(kind: str, message: str) -> None:
    st.session_state["flash"] = (kind, message)


def input_notes(name_cut: bool, qty_fixed: bool, qty: int) -> str:
    notes = []
    if name_cut:
        notes.append(f"이름은 {MAX_NAME_LEN}자까지만 저장돼요")
    if qty_fixed:
        notes.append(f"수량은 {MIN_QTY}~{MAX_QTY}개까지라서 {qty}개로 맞췄어요")
    return ". ".join(notes) + "." if notes else ""


def on_add() -> None:
    name, name_cut = clean_name(st.session_state.get("new_name"))
    qty, qty_fixed = clean_qty(st.session_state.get("new_qty"))
    if not name:
        flash("warning", "아이템 이름을 입력해주세요.")
        return
    if len(st.session_state["items"]) >= MAX_ITEMS:
        flash("warning", f"아이템은 최대 {MAX_ITEMS}개까지 추가할 수 있어요. 다 산 항목을 정리해주세요.")
        return
    add_item(name, qty)
    note = input_notes(name_cut, qty_fixed, qty)
    if note:
        flash("info", note)
    st.toast(f"'{safe_text(name)}' 추가했어요.", icon="✅")


def on_edit_start(item_id: str) -> None:
    item = find_item(item_id)
    if item is None:
        return
    st.session_state["editing_id"] = item_id
    st.session_state[f"edit_name_{item_id}"] = item["name"]
    st.session_state[f"edit_qty_{item_id}"] = item["quantity"]


def on_edit_save(item_id: str) -> None:
    name, name_cut = clean_name(st.session_state.get(f"edit_name_{item_id}"))
    qty, qty_fixed = clean_qty(st.session_state.get(f"edit_qty_{item_id}"))
    if not name:
        flash("warning", "아이템 이름을 입력해주세요.")
        return
    update_item(item_id, name, qty)
    st.session_state["editing_id"] = None
    note = input_notes(name_cut, qty_fixed, qty)
    if note:
        flash("info", note)


def on_edit_cancel() -> None:
    st.session_state["editing_id"] = None


def on_toggle(item_id: str) -> None:
    toggle_item(item_id, st.session_state[f"chk_{item_id}"])


def on_delete(item_id: str) -> None:
    delete_item(item_id)
    st.toast("삭제했어요.", icon="🗑️")


def on_clear_checked() -> None:
    count = sum(1 for i in st.session_state["items"] if i["checked"])
    clear_checked()
    st.toast(f"완료한 {count}개를 삭제했어요.", icon="🧹")


# ---------- 렌더링 ----------

def safe_text(text: str) -> str:
    """사용자 입력이 마크다운으로 해석되지 않고 글자 그대로 보이도록 처리한다.

    - 마크다운/HTML 엔티티 기호를 이스케이프한다 (& 포함).
    - URL/이메일 자동 링크는 보이지 않는 문자(ZWSP)로 끊는다.
    """
    text = re.sub(r"(?i)(?:https?|ftp)(?=://)", lambda m: m.group(0) + ZWSP, text)
    text = re.sub(r"(?i)www(?=\.)", lambda m: m.group(0) + ZWSP, text)
    text = text.replace("@", "@" + ZWSP)
    return re.sub(r"([\\`*_{}\[\]()#+\-.!~|<>:$&])", r"\\\1", text)


def item_label(item: dict) -> str:
    text = f"{safe_text(item['name'])} x{item['quantity']}"
    return f"~~{text}~~" if item["checked"] else text


CSS = """
<style>
.block-container { padding-top: 2.5rem; max-width: 640px; }
/* 모바일에서도 컬럼을 가로로 유지 (Streamlit 기본은 좁은 화면에서 세로로 쌓임) */
div[data-testid="stHorizontalBlock"] { flex-wrap: nowrap !important; gap: 0.5rem !important; }
div[data-testid="stColumn"] { min-width: 0 !important; }
/* 수정/삭제 버튼 열은 고정 폭, 터치하기 쉬운 높이(44px 이상) */
div[data-testid="stColumn"]:has(button[kind="tertiary"]) { flex: 0 0 3.4rem !important; width: 3.4rem !important; }
div[data-testid="stColumn"]:has(button[kind="tertiary"]) div[data-testid="stElementContainer"],
div[data-testid="stColumn"]:has(button[kind="tertiary"]) div[data-testid="stButton"] { width: 100% !important; }
div[data-testid="stColumn"] button[kind="tertiary"] { min-height: 2.75rem; padding: 0 0.25rem; width: 100%; }
/* 긴 이름은 말줄임 대신 줄바꿈 */
div[data-testid="stCheckbox"] label p { white-space: normal; overflow-wrap: anywhere; }
/* 완료 항목: 취소선은 마크다운(~~)이 처리하고, 색은 읽을 수 있는 회색으로 (대비 4.5:1 이상) */
div[data-testid="stCheckbox"]:has(input:checked) label p { color: #6b7280; }
/* 아주 좁은 화면에서는 제목이 줄바꿈되지 않게 줄인다 */
@media (max-width: 360px) { div[data-testid="stMainBlockContainer"] h1 { font-size: 1.8rem !important; } }
</style>
"""


def render_flash() -> None:
    flash_msg = st.session_state.pop("flash", None)
    if flash_msg:
        kind, message = flash_msg
        getattr(st, kind)(message)


def render_header() -> None:
    st.title("🛒 쇼핑 리스트")
    st.caption("필요한 물건을 적고, 장 보면서 체크하세요.")


def render_add_form() -> None:
    with st.form("add_form", clear_on_submit=True, border=True):
        name_col, qty_col = st.columns([5, 2], vertical_alignment="bottom")
        name_col.text_input("아이템 이름", key="new_name", placeholder="예: 우유")
        # 범위를 막으면 오류 안내 없이 추가가 안 되므로, 넓게 받고 서버에서 맞추면서 알려준다
        qty_col.number_input("수량", key="new_qty", value=1, step=1, min_value=-10**6, max_value=10**6)
        st.form_submit_button("추가", on_click=on_add, type="primary", use_container_width=True)
    st.caption(f"이름 {MAX_NAME_LEN}자 · 수량 {MIN_QTY}~{MAX_QTY} · 최대 {MAX_ITEMS}개")


def render_edit_row(item: dict) -> None:
    item_id = item["id"]
    with st.container(border=True):
        # 폼으로 감싸서 Enter 키로도 저장되게 한다 (첫 번째 제출 버튼 = 저장)
        with st.form(f"edit_form_{item_id}", border=False):
            name_col, qty_col = st.columns([3, 1], vertical_alignment="bottom")
            name_col.text_input("이름", key=f"edit_name_{item_id}")
            qty_col.number_input(
                "수량", key=f"edit_qty_{item_id}", step=1, min_value=-10**6, max_value=10**6
            )
            save_col, cancel_col = st.columns(2)
            save_col.form_submit_button(
                "저장", on_click=on_edit_save, args=(item_id,), type="primary", use_container_width=True
            )
            cancel_col.form_submit_button("취소", on_click=on_edit_cancel, use_container_width=True)


def render_item_row(item: dict) -> None:
    item_id = item["id"]
    key = f"chk_{item_id}"
    if st.session_state.get(key) != item["checked"]:
        st.session_state[key] = item["checked"]  # 다른 탭에서 바뀐 체크 상태를 반영
    with st.container(border=True):
        check_col, edit_col, del_col = st.columns([1, 1, 1], vertical_alignment="center")
        check_col.checkbox(item_label(item), key=key, on_change=on_toggle, args=(item_id,))
        edit_col.button("수정", key=f"edit_{item_id}", on_click=on_edit_start, args=(item_id,), type="tertiary")
        del_col.button("삭제", key=f"del_{item_id}", on_click=on_delete, args=(item_id,), type="tertiary")


def render_item_list() -> None:
    items = st.session_state["items"]
    if not items:
        st.info("아직 아이템이 없어요. 첫 아이템을 추가해보세요!", icon="📝")
        return
    # 미체크 항목을 위로, 체크 항목을 아래로 (각 그룹 내 추가 순서 유지)
    todo = [i for i in items if not i["checked"]]
    done = [i for i in items if i["checked"]]
    editing_id = st.session_state["editing_id"]
    for title, group in (("남은 항목", todo), ("완료한 항목", done)):
        if not group:
            continue
        st.caption(f"**{title}** · {len(group)}")
        for item in group:
            if item["id"] == editing_id:
                render_edit_row(item)
            else:
                render_item_row(item)


def render_summary() -> None:
    items = st.session_state["items"]
    total = len(items)
    done = sum(1 for i in items if i["checked"])
    with st.container(border=True):
        st.progress(done / total if total else 0.0, text=f"전체 {total}개 중 {done}개 완료")
        st.button(
            "체크된 항목 모두 삭제", on_click=on_clear_checked,
            disabled=done == 0, use_container_width=True,
        )


def main() -> None:
    init_state()
    sync_items()  # 콜백이 먼저 실행되므로 여기서는 항상 최신 상태를 병합해 저장한다
    cleanup_old_files()
    st.html(CSS)  # 고정 스타일만 사용 (사용자 입력은 HTML로 넣지 않는다)
    render_header()
    render_flash()
    render_add_form()
    render_item_list()
    render_summary()


main()
