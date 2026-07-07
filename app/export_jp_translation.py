"""
Usage: python export_jp_translation.py <image_path> [output_dir]

画像内の全カードをGemini APIで認識し、AlteredTcgJp.csv の翻訳データを使って
和訳シールの PDF を出力する。
"""
import sys
import os
import json
import re
from pathlib import Path
import base64
import urllib.request

import numpy as np
import polars as pl

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from app.config import GEMINI_API_KEY, CSV_PATH, UNIQUES_CSV_PATH, OUTPUT_DIR
from app.csv_manager import load_csv, load_uniques
from app.prompts import RECOGNIZE_PROMPT

blackets_match = re.compile("\(.*?\)")

def _call_gemini(api_key, prompt, image_bytes=None, mime_type="image/jpeg", max_retries=3):
    """Gemini 3.0 Flash API を呼び出す。429時はリトライする"""
    import time

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-3.5-flash:generateContent?key={api_key}"
    )

    parts = []

    if image_bytes is not None:
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        parts.append({"inline_data": {"mime_type": mime_type, "data": b64}})

    parts.append({"text": prompt})

    payload = json.dumps({
        "contents": [{"parts": parts}]
    }).encode("utf-8")

    for attempt in range(max_retries):
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            return result["candidates"][0]["content"]["parts"][0]["text"]
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8")
            # retry-after の秒数をエラーメッセージから取得
            retry_after = 60
            m = re.search(r"retry in (\d+\.?\d*)", body)
            if m:
                retry_after = int(float(m.group(1))) + 5
            print(f"HTTP {e.code}: {body[:200]}")
            if e.code == 429:
                wait = retry_after if attempt == 0 else retry_after + 2 ** attempt
                print(f"レート制限 (429)。{wait}秒後にリトライ... ({attempt + 1}/{max_retries})")
                time.sleep(wait)
            else:
                raise
    raise RuntimeError("Gemini API: リトライ上限に達しました")


def recognize_all_cards(api_key, image_path):
    """画像内の全カードを認識してリストで返す"""
    with open(image_path, "rb") as f:
        image_bytes = f.read()

    ext = Path(image_path).suffix.lower().lstrip(".")
    mime_map = {"jpg": "image/jpeg", "jpeg": "image/jpeg",
                "png": "image/png", "webp": "image/webp", "gif": "image/gif"}
    mime = mime_map.get(ext, "image/jpeg")

    response = _call_gemini(api_key, RECOGNIZE_PROMPT, image_bytes, mime)
    response = response.strip()
    response = re.sub(r"^```[a-z]*\n?", "", response)
    response = re.sub(r"\n?```$", "", response)
    return json.loads(response)


# csv_data を DataFrame にキャッシュする（同一オブジェクトなら再構築しない）
_df_cache: tuple | None = None  # (id(csv_data), pl.DataFrame, list[row])

_PRIMARY_LEV_FIELDS = [
    # (card_info キー,  CSV列名,          重み)
    ("card_name",      "英語名",          4),
]

_EXACT_FIELDS = [
    # (card_info キー,  CSV列名,          重み)
    ("rarity_ocr",     "レアリティ",      1),
    ("rarity_symbol",  "レアリティ",      0.5),
    ("faction",        "陣営",            1),
    ("main_cost",      "手札コスト",      1),
    ("recall_cost",    "リザーブコスト",  1),
    ("forest",         "森",              0.5),
    ("mountain",       "山",              0.5),
    ("ocean",          "海",              0.5),
]

_SECONDARY_LEV_FIELDS = [
    # (card_info キー,  CSV列名,          重み)
    ("card_number",    "カード番号",      1),
    ("unique_number",  "ユニーク番号",    1),
    ("card_type",      "カードタイプ",    1),
    ("_subtypes",      "サブタイプ",      1),  # "_subtypes" は特別処理
    ("card_text",      "英語能力",        2),
]
_ALL_CSV_COLS = [c for _, c, _ in _PRIMARY_LEV_FIELDS] + [c for _, c, _ in _SECONDARY_LEV_FIELDS] + [c for _, c, _ in _EXACT_FIELDS]


def _build_df(csv_data: dict) -> tuple:
    rows = list(csv_data.values())

    def col(key):
        return [str(r.get(key) or "").strip() for r in rows]

    df = pl.DataFrame({c: col(c) for c in _ALL_CSV_COLS})
    return df, rows


def _get_df(csv_data: dict) -> tuple:
    global _df_cache
    if _df_cache is None or _df_cache[0] != id(csv_data):
        df, rows = _build_df(csv_data)
        _df_cache = (id(csv_data), df, rows)
    return _df_cache[1], _df_cache[2]


def _strip_parens(s: str) -> str:
    """かっこ書き（注釈文など）を取り除く"""
    return blackets_match.sub("", s)


def _lev_dist(a: str, b: str) -> int:
    """numpy配列ベースのレーベンシュタイン距離（かっこ書きは比較対象外）"""
    a = _strip_parens(a)
    b = _strip_parens(b)
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if la == 0:
        return lb
    if lb == 0:
        return la
    prev = np.arange(lb + 1, dtype=np.int32)
    curr = np.empty(lb + 1, dtype=np.int32)
    for i, ca in enumerate(a):
        curr[0] = i + 1
        for j, cb in enumerate(b):
            curr[j + 1] = min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (ca != cb))
        prev, curr = curr, prev
    return int(prev[lb])


_TOP_K = 20  # 一次フィルタで残す候補数


def _score_exact_and_name(card_info: dict, df: pl.DataFrame) -> tuple:
    """
    一次フィルタ用スコア: 完全一致フィールド全部 + 英語名（レーベンシュタイン）のみ計算。
    全行に対して実行し、(生の重み付き合計スコア, 合計重み) の配列を返す。
    正規化（割り算）はせず、呼び出し側でほかのスコアと合算してから行う。
    """
    n = len(df)
    total_score = np.zeros(n, dtype=np.float64)
    total_weight = np.zeros(n, dtype=np.float64)

    # 英語名（レーベンシュタイン）
    for ocr_key, csv_col, weight in _PRIMARY_LEV_FIELDS:
        ocr_val = (card_info.get(ocr_key) or "").strip()
        if not ocr_val:
            continue
        csv_vals = df[csv_col].to_list()
        la = len(ocr_val)
        sims = np.array([
            _lev_dist(ocr_val, v) / max(la, len(v)) if v else 1.0
            for v in csv_vals
        ], dtype=np.float64)
        total_score += sims * weight
        total_weight += weight

    # 完全一致フィールド（polarsベクトル演算）
    for ocr_key, csv_col, weight in _EXACT_FIELDS:
        ocr_val = (card_info.get(ocr_key) or "").strip()
        if not ocr_val:
            continue
        col = df[csv_col]
        has_val = (col != "").to_numpy()
        mismatch = (col != ocr_val).to_numpy().astype(np.float64)
        total_score += mismatch * has_val * weight
        total_weight += has_val * weight

    return total_score, total_weight


def _score_all(card_info: dict, df: pl.DataFrame) -> np.ndarray:
    """
    全CSV行に対する距離スコアをnumpy配列で一括計算する（0=完全一致、1=完全不一致）。
    一次フィルタで上位 _TOP_K 件に絞ってから残りのレーベンシュタインフィールドを計算する。
    """
    subtypes_ocr = card_info.get("card_subtypes") or []
    if isinstance(subtypes_ocr, list):
        subtypes_ocr = "/".join(subtypes_ocr)

    if card_info.get("rarity_symbol") == "R" and card_info.get("rarity_ocr") == "F":
        card_info["rarity_symbol"] = "F"

    n = len(df)

    # ── 一次フィルタ: 完全一致 + 英語名（生の重み付き合計・合計重み） ──
    first_score, first_weight = _score_exact_and_name(card_info, df)
    with np.errstate(invalid="ignore", divide="ignore"):
        first_ratio = np.where(first_weight > 0, first_score / first_weight, 1.0)
    if n > _TOP_K:
        top_indices = np.argpartition(first_ratio, _TOP_K)[:_TOP_K]
    else:
        top_indices = np.arange(n)
    df_top = df[top_indices]

    # ── 二次スコア: 一次フィルタの生スコアに残りのレーベンシュタインフィールドを追加 ──
    # （first_score/first_weight はまだ正規化していない生の値なので、そのまま加算してよい）
    total_score = first_score[top_indices].copy()
    total_weight = first_weight[top_indices].copy()

    # 残りのレーベンシュタインフィールド（かっこ書きは比較対象外）
    for ocr_key, csv_col, weight in _SECONDARY_LEV_FIELDS:
        ocr_val = subtypes_ocr if ocr_key == "_subtypes" else (card_info.get(ocr_key) or "").strip()
        if not ocr_val:
            continue
        csv_vals = df_top[csv_col].to_list()
        ocr_stripped = _strip_parens(ocr_val)
        la = len(ocr_stripped)
        sims = np.array([
            _lev_dist(ocr_val, v) / max(la, len(_strip_parens(v))) if v else 1.0
            for v in csv_vals
        ], dtype=np.float64)
        total_score += sims * weight
        total_weight += weight

    mask = total_weight > 0
    top_result = np.ones(len(top_indices), dtype=np.float64)
    top_result[mask] = total_score[mask] / total_weight[mask]

    # 全行スコアに書き戻す（足切りされた行は 1.0 のまま）
    result = np.ones(n, dtype=np.float64)
    result[top_indices] = top_result
    return result


def lookup_translation(card_info, csv_data, uniques_data):
    """
    カード情報から翻訳行を返す。見つからなければ None。

    ユニークカード（rarity=U）はカード番号＋ユニーク番号で直接検索する。
    それ以外は、各フィールドの類似度スコア（0=完全一致）を重み付き平均して
    最もスコアが低い行をマッチ結果とする。

    重み: card_name・card_text = 4、その他 = 1
    """
    rarity_ocr = (card_info.get("rarity_ocr") or "").strip()
    rarity_symbol = (card_info.get("rarity_symbol") or "").strip()

    df, rows = _get_df(csv_data)
    scores = _score_all(card_info, df)

    # ユニークカード: カード番号 + ユニーク番号で直接検索
    if rarity_ocr == "U" or rarity_symbol == "U":
        df_unique, rows_unique = _get_df(uniques_data)
        scores_unique = _score_all(card_info, df_unique)
        if np.min(scores_unique) <= np.min(scores):
            return rows_unique[int(np.argmin(scores_unique))]

    return rows[int(np.argmin(scores))]


def generate_pdf_direct(sticker_cards, pdf_path):
    """
    reportlab canvas で和訳シール PDF を直接生成する。
    A4 縦・3カラム（各 63mm）・游ゴシック Light 6pt。
    _xxx_ は下線付きテキストとして canvas.line() で描画。
    列優先（各カラムを上から下へ詰めてから次のカラムへ）でカードを配置し、
    カラムの残り高さにカード1枚が収まらない場合はそのカラムを打ち切って
    次のカラムへ送る（カードが分断されることはない）。
    カードのセル高さが5cmを超える場合はフォントサイズを縮小して5cm以下に収める。
    カラム境界に破線の切り取り線を引く。
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas as rl_canvas
    import os, re

    # 游ゴシック Light を登録
    font_path = r"C:\Windows\Fonts\YuGothL.ttc"
    if not os.path.exists(font_path):
        font_path = r"C:\Windows\Fonts\meiryo.ttc"
    pdfmetrics.registerFont(TTFont("YuGothL", font_path))

    # ── レイアウト定数 ──────────────────────────────────────
    page_w, page_h = A4
    CARD_W = 63 * mm          # MTG スタンダードサイズと同じ横幅
    N_COLS = 3
    L_MARGIN = (page_w - N_COLS * CARD_W) / 2   # 左右マージン（自動計算）
    T_MARGIN = 10 * mm
    B_MARGIN = 10 * mm
    USABLE_H = page_h - T_MARGIN - B_MARGIN     # 1カラムあたりの使用可能な高さ

    FONT = "YuGothL"
    HEADER_SIZE = 6.5
    BODY_SIZE = 6.0
    HEADER_LEADING_RATIO = 1.35
    LEADING_RATIO = 1.45
    PAD_X = 1.5 * mm          # セル内の左右パディング
    PAD_Y = 1.0 * mm          # セル内の上下パディング
    TEXT_W = CARD_W - 2 * PAD_X

    MAX_CARD_H = 50 * mm      # カード1枚あたりの最大の縦幅（5cm）
    MIN_FONT_SCALE = 0.5      # フォント縮小の下限（元サイズの50%まで）
    FONT_SCALE_STEP = 0.02

    # ── _xxx_ をランのリストに分解 ──────────────────────────
    def parse_runs(text):
        """[(文字列, underline:bool), ...] を返す"""
        runs = []
        for part in re.split(r"(_[^_]+_)", text):
            if part.startswith("_") and part.endswith("_") and len(part) > 2:
                runs.append((part[1:-1], True))
            elif part:
                runs.append((part, False))
        return runs

    # ── ランリストを行単位に折り返す ────────────────────────
    def wrap_runs(runs, font, size, max_w):
        """
        CJK 文字を1文字ずつ折り返す。
        返り値: [[(文字列, bool), ...], ...]  ← 行ごとのランリスト
        """
        lines = []
        current_line = []
        current_w = 0.0

        for text, ul in runs:
            for ch in text:
                ch_w = pdfmetrics.stringWidth(ch, font, size)
                if current_w + ch_w > max_w and current_line:
                    lines.append(current_line)
                    current_line = []
                    current_w = 0.0
                # 同じ下線状態なら直前のランに結合
                if current_line and current_line[-1][1] == ul:
                    prev_text, prev_ul = current_line[-1]
                    current_line[-1] = (prev_text + ch, prev_ul)
                else:
                    current_line.append((ch, ul))
                current_w += ch_w

        if current_line:
            lines.append(current_line)
        return lines

    # ── カードのセル高さを計算（フォントサイズを指定可能） ──
    def measure_card_h(card, header_size, body_size):
        header_leading = header_size * HEADER_LEADING_RATIO
        leading = body_size * LEADING_RATIO

        header_text = f"{card['card_number']}-{card['rarity']} {card['name_jp']}"
        n_header_lines = len(wrap_runs(parse_runs(header_text), FONT, header_size, TEXT_W))
        n_header_lines = max(n_header_lines, 1)

        ability = card.get("ability_jp") or ""
        n_body_lines = 0
        for raw_line in ability.splitlines():
            wrapped = wrap_runs(parse_runs(raw_line), FONT, body_size, TEXT_W)
            n_body_lines += max(len(wrapped), 1)

        h = (PAD_Y
             + n_header_lines * header_leading
             + n_body_lines * leading
             + PAD_Y)
        return h

    # ── カードごとのフォントサイズを決定（5cm を超えるなら縮小） ──
    def fit_font_size(card):
        scale = 1.0
        while scale > MIN_FONT_SCALE:
            header_size = HEADER_SIZE * scale
            body_size = BODY_SIZE * scale
            h = measure_card_h(card, header_size, body_size)
            if h <= MAX_CARD_H:
                return header_size, body_size, h
            scale -= FONT_SCALE_STEP
        # 下限まで縮小しても収まらない場合は下限サイズのまま返す
        header_size = HEADER_SIZE * MIN_FONT_SCALE
        body_size = BODY_SIZE * MIN_FONT_SCALE
        h = measure_card_h(card, header_size, body_size)
        return header_size, body_size, h

    # ── 1ランの行を描画 ────────────────────────────────────
    def draw_run_line(c, run_line, x, baseline_y, font, size):
        """run_line = [(str, underline), ...] を x,baseline_y に描画"""
        cx = x
        for text, ul in run_line:
            w = pdfmetrics.stringWidth(text, font, size)
            c.setFont(font, size)
            c.drawString(cx, baseline_y, text)
            if ul:
                ul_y = baseline_y - 0.5
                c.setLineWidth(0.4)
                c.setDash([])
                c.line(cx, ul_y, cx + w, ul_y)
            cx += w

    # ── 切り取り線 ────────────────────────────────────────
    def set_cut_dash(c):
        c.setDash([2, 2])
        c.setLineWidth(0.3)
        c.setStrokeColorRGB(0.5, 0.5, 0.5)

    def draw_col_border(c, x, y_top, h):
        """カラム左右の垂直破線（切り取り線）"""
        set_cut_dash(c)
        c.line(x, y_top - h, x, y_top)
        c.line(x + CARD_W, y_top - h, x + CARD_W, y_top)

    def draw_hline(c, x, y):
        """カラム内、カード間の水平破線（切り取り線）"""
        set_cut_dash(c)
        c.line(x, y, x + CARD_W, y)

    # ── 1枚のカードセルを描画 ──────────────────────────────
    def draw_card_cell(c, card, x, y_top, header_size, body_size):
        """カードを (x, y_top) から下向きに描画。次の y_top（セル下端）を返す"""
        header_leading = header_size * HEADER_LEADING_RATIO
        leading = body_size * LEADING_RATIO

        y = y_top - PAD_Y

        # ヘッダー行
        header_text = f"{card['card_number']}-{card['rarity']} {card['name_jp']}"
        for run_line in wrap_runs(parse_runs(header_text), FONT, header_size, TEXT_W):
            y -= header_leading
            draw_run_line(c, run_line, x + PAD_X, y, FONT, header_size)

        # 能力テキスト
        ability = card.get("ability_jp") or ""
        for raw_line in ability.splitlines():
            wrapped = wrap_runs(parse_runs(raw_line), FONT, body_size, TEXT_W)
            if not wrapped:
                y -= leading
                continue
            for run_line in wrapped:
                y -= leading
                draw_run_line(c, run_line, x + PAD_X, y, FONT, body_size)

        return y - PAD_Y

    # ── 事前計算: カードごとのフォントサイズとセル高さ ──────
    layouts = []
    for card in sticker_cards:
        header_size, body_size, h = fit_font_size(card)
        layouts.append({
            "card": card,
            "header_size": header_size,
            "body_size": body_size,
            "height": h,
        })

    # ── メイン描画ループ（列優先でカラムを下まで詰める） ────
    c = rl_canvas.Canvas(pdf_path, pagesize=A4)

    idx = 0
    n = len(layouts)
    is_first_page = True

    while idx < n:
        if not is_first_page:
            c.showPage()
        is_first_page = False

        for col in range(N_COLS):
            if idx >= n:
                break

            x = L_MARGIN + col * CARD_W
            col_top = page_h - T_MARGIN
            col_used = 0.0
            placed = []

            while idx < n:
                item = layouts[idx]
                h = item["height"]
                # カラムが空でなく、かつ残り高さに収まらないなら次のカラムへ送る
                # （カード1枚がカラムをまたいで分断されることを防ぐ）
                if placed and col_used + h > USABLE_H:
                    break
                placed.append(item)
                col_used += h
                idx += 1

            # カラム内のカードを上から順に描画
            cur_y = col_top
            for i, item in enumerate(placed):
                cur_y = draw_card_cell(
                    c, item["card"], x, cur_y,
                    item["header_size"], item["body_size"],
                )
                if i < len(placed) - 1:
                    draw_hline(c, x, cur_y)

            # カラムの外枠（縦の切り取り線）
            if placed:
                draw_col_border(c, x, col_top, col_used)

    c.save()
    return pdf_path


def main():
    if len(sys.argv) < 2:
        print("Usage: python export_jp_translation.py <image_path> [output_dir]")
        sys.exit(1)

    image_path = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else (OUTPUT_DIR or ".")

    if not os.path.exists(image_path):
        print(f"エラー: 画像ファイルが見つかりません: {image_path}")
        sys.exit(1)

    if not GEMINI_API_KEY:
        print("エラー: GEMINI_API_KEY が設定されていません。.env ファイルを確認してください。")
        sys.exit(1)

    # --- Step 1: カード認識 ---
    print(f"画像を解析中: {image_path}")
    try:
        cards_raw = recognize_all_cards(GEMINI_API_KEY, image_path)
    except Exception as e:
        print(f"カード認識エラー: {e}")
        sys.exit(1)
    print(f"{len(cards_raw)} 枚のカードを検出しました。")

    # --- Step 2: 翻訳データ検索 ---
    log_path = os.path.join(output_dir, f"ocr_log.txt")
    with open(log_path, "wt", encoding="utf-8") as f:
        json.dump(cards_raw, f, indent=4)
        
    csv_data = load_csv(CSV_PATH)
    uniques_data = load_uniques(UNIQUES_CSV_PATH) if os.path.exists(UNIQUES_CSV_PATH) else {}

    sticker_cards = []
    not_found = []

    for card_info in cards_raw:
        row = lookup_translation(card_info, csv_data, uniques_data)
        name = card_info.get("card_name") or "不明"
        if row:
            sticker_cards.append({
                "card_number": row["カード番号"],
                "rarity":      row["レアリティ"],
                "name_jp":     row["日本語名"],
                "ability_jp":  row["能力"],
            })
            print(f"  ✓  {name}  → {row['カード番号']}-{row['レアリティ']} {row['日本語名']}")
        else:
            not_found.append(card_info)
            print(f"  ✗  {name}  [card_number={card_info.get('card_number')}] — 翻訳データなし")

    print(f"\n翻訳データ: {len(sticker_cards)}/{len(cards_raw)} 枚")

    if not sticker_cards:
        print("出力するカードがありません。終了します。")
        sys.exit(1)

    # --- Step 3: PDF 生成 ---
    from datetime import datetime
    timestamp = datetime.now().strftime("%Y%m%d-%H%M")
    pdf_path = os.path.join(output_dir, f"{timestamp}和訳シール.pdf")

    try:
        generate_pdf_direct(sticker_cards, pdf_path)
        print(f"PDF を出力: {pdf_path}")
    except Exception as e:
        print(f"PDF 生成エラー: {e}")

    if not_found:
        print(f"\n翻訳データが見つからなかったカード ({len(not_found)} 枚):")
        for c in not_found:
            print(f"  {c.get('card_name','?')} / {c.get('rarity','?')} / {c.get('faction','?')}")


if __name__ == "__main__":
    main()
