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

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from app.config import GEMINI_API_KEY, CSV_PATH, UNIQUES_CSV_PATH, OUTPUT_DIR
from app.csv_manager import load_csv, load_uniques
from app.card_recognizer import _call_gemini


RECOGNIZE_PROMPT = """この画像にはAltered TCGのカードが複数枚含まれています。
画像に写っているカードをすべてリストアップし、各カードについて以下の情報をJSON配列で返してください。

【カード番号・レアリティの読み取り】
カードの下部にある識別文字列（例: BTG-052-R）から読み取ってください。
レアリティ: C（コモン）/ R（レア）/ F（色違い）/ E（エグザルテッド）/ H（ヒーロー）/ U（ユニーク）/ T（トークン）

通常カード: 「BTG-052-R」→ card_number=BTG-052, rarity=R, unique_number=null
ユニーク:   「ROC-102-U-18245」→ card_number=ROC-102, rarity=U, unique_number=18245

カード番号が読み取れない場合は card_number=null とし、宝石マーク・旗マークの色でレアリティと陣営を判定してください。

【宝石マーク（カード名の上）によるレアリティ判定】
・宝石マークなし（白い円） → C / H / T のいずれか（カード名で区別できます）
・青色の宝石マーク → R または F（旗マークの色で区別）
・銅色の宝石マーク → E（エグザルテッド）
・金色の宝石マーク → U（ユニーク）

【旗マーク（カード右上）による陣営・R/F判定】
・茶色 → Axiom
・赤   → Bravos
・ピンク → Lyra
・緑   → Muna
・青   → Ordis
・紫   → Yzmir

同名カードで陣営が異なる方が F（色違い）、同名コモンカードと同じ陣営の方が R（レア）です。

【出力形式】
[
  {
    "card_number": "BTG-052（読み取れない場合はnull）",
    "rarity": "R（読み取れない場合は宝石マークから推定）",
    "unique_number": "ユニーク番号（非ユニークはnull）",
    "card_name": "カード上部の英語カード名",
    "faction": "Axiom / Bravos / Lyra / Muna / Ordis / Yzmir（旗マークから判定）"
  }
]

JSON配列のみ返してください。マークダウンのコードブロックは不要です。"""

def _call_gemini(api_key, prompt, image_bytes=None, mime_type="image/jpeg", max_retries=3):
    """Gemini 2.0 Flash API を呼び出す。429時はリトライする"""
    import time

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.5-flash:generateContent?key={api_key}"
    )

    parts = [{"text": prompt}]
    if image_bytes is not None:
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        parts.append({"inline_data": {"mime_type": mime_type, "data": b64}})

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


def lookup_translation(card_info, csv_data, uniques_data):
    """
    カード情報から翻訳行を返す。見つからなければ None。

    検索優先順位:
      1. ユニークカード: カード番号 + ユニーク番号
      2. 英語名 + レアリティ + 陣営（カード名は大きく明瞭なためOCR精度が高い）
      3. カード番号 + レアリティ（番号はOCRで誤読されやすいため補助的に使用）

    レアリティ曖昧ケースの処理:
      識別文字列が不明でOCRがデフォルト値を返した場合、カード名と陣営でCSVと突合して正確なレアリティを取得する。
      ・C（宝石なし）→ C / H / T の可能性あり。カード名で一意に特定できる。
      ・R（青宝石）→ R / F の可能性あり。カード名 + 陣営で特定する（同名で陣営が異なればF）。
    """
    rarity = (card_info.get("rarity") or "").strip()
    card_number = (card_info.get("card_number") or "").strip()
    unique_number = card_info.get("unique_number")
    card_name = (card_info.get("card_name") or "").strip()
    faction = (card_info.get("faction") or "").strip()

    # 1. ユニークカード: カード番号 + ユニーク番号
    if rarity == "U" and card_number and unique_number:
        row = uniques_data.get((card_number, str(unique_number)))
        if row:
            return row

    # 2. 英語名 + レアリティ + 陣営（優先）
    if card_name:
        name_lower = card_name.lower()
        candidates = [r for r in csv_data.values()
                      if r.get("英語名", "").lower() == name_lower]
        if candidates:
            # レアリティでの絞り込み:
            # OCRがデフォルト C を返した場合は C/H/T すべてを候補に残す（カード名で一意に決まる）
            # OCRがデフォルト R を返した場合は R/F を候補に残し、陣営で絞り込む
            if rarity == "C":
                filtered = [r for r in candidates if r["レアリティ"] in ("C", "H", "T")]
                if filtered:
                    candidates = filtered
            elif rarity == "R":
                filtered = [r for r in candidates if r["レアリティ"] in ("R", "F")]
                if filtered:
                    candidates = filtered
            elif rarity:
                filtered = [r for r in candidates if r["レアリティ"] == rarity]
                if filtered:
                    candidates = filtered

            # 陣営で絞り込む（R/F の区別と F の特定に使用）
            if faction:
                filtered = [r for r in candidates if r.get("陣営", "") == faction]
                if filtered:
                    candidates = filtered

            return candidates[0]

    # 3. カード番号 + レアリティ（フォールバック）
    if card_number and rarity:
        row = csv_data.get((card_number, rarity))
        if row:
            return row

    return None


def generate_pdf_direct(sticker_cards, pdf_path):
    """
    reportlab canvas で和訳シール PDF を直接生成する。
    A4 縦・3カラム（各 63mm）・游ゴシック Light 6pt。
    _xxx_ は下線付きテキストとして canvas.line() で描画。
    カラム境界と行間に破線の切り取り線を引く。
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

    FONT = "YuGothL"
    HEADER_SIZE = 6.5
    BODY_SIZE = 6.0
    LEADING = BODY_SIZE * 1.45
    HEADER_LEADING = HEADER_SIZE * 1.35
    PAD_X = 1.5 * mm          # セル内の左右パディング
    PAD_Y = 1.0 * mm          # セル内の上下パディング
    TEXT_W = CARD_W - 2 * PAD_X

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

    # ── カードのセル高さを計算 ──────────────────────────────
    def measure_card_h(card):
        header_text = f"{card['card_number']}-{card['rarity']} {card['name_jp']}"
        n_header_lines = len(wrap_runs(parse_runs(header_text), FONT, HEADER_SIZE, TEXT_W))
        n_header_lines = max(n_header_lines, 1)

        ability = card.get("ability_jp") or ""
        n_body_lines = 0
        for raw_line in ability.splitlines():
            wrapped = wrap_runs(parse_runs(raw_line), FONT, BODY_SIZE, TEXT_W)
            n_body_lines += max(len(wrapped), 1)

        h = (PAD_Y
             + n_header_lines * HEADER_LEADING
             + n_body_lines * LEADING
             + PAD_Y)
        return h

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

    def draw_hline(c, y):
        """水平破線（行間の切り取り線）"""
        set_cut_dash(c)
        c.line(L_MARGIN, y, L_MARGIN + N_COLS * CARD_W, y)

    def draw_vlines(c, y_top, h):
        """垂直破線（カラム境界の切り取り線）"""
        set_cut_dash(c)
        for i in range(N_COLS + 1):
            x = L_MARGIN + i * CARD_W
            c.line(x, y_top - h, x, y_top)

    # ── 1枚のカードセルを描画 ──────────────────────────────
    def draw_card_cell(c, card, x, y_top):
        """カードを (x, y_top) から下向きに描画。次の y_top を返す"""
        y = y_top - PAD_Y

        # ヘッダー行
        header_text = f"{card['card_number']}-{card['rarity']} {card['name_jp']}"
        for run_line in wrap_runs(parse_runs(header_text), FONT, HEADER_SIZE, TEXT_W):
            y -= HEADER_LEADING
            draw_run_line(c, run_line, x + PAD_X, y, FONT, HEADER_SIZE)

        # 能力テキスト
        ability = card.get("ability_jp") or ""
        for raw_line in ability.splitlines():
            wrapped = wrap_runs(parse_runs(raw_line), FONT, BODY_SIZE, TEXT_W)
            if not wrapped:
                y -= LEADING
                continue
            for run_line in wrapped:
                y -= LEADING
                draw_run_line(c, run_line, x + PAD_X, y, FONT, BODY_SIZE)

        return y - PAD_Y

    # ── メイン描画ループ ────────────────────────────────────
    c = rl_canvas.Canvas(pdf_path, pagesize=A4)

    # カードを N_COLS 枚ずつの行にグループ化
    rows = [sticker_cards[i:i + N_COLS] for i in range(0, len(sticker_cards), N_COLS)]

    y_cursor = page_h - T_MARGIN  # 現在の描画 y 位置（上から下へ）

    for row_cards in rows:
        row_h = max(measure_card_h(card) for card in row_cards)

        # ページ不足なら改ページ
        if y_cursor - row_h < B_MARGIN:
            c.showPage()
            y_cursor = page_h - T_MARGIN

        draw_vlines(c, y_cursor, row_h)

        # 各カードを列に配置
        for col_idx, card in enumerate(row_cards):
            x = L_MARGIN + col_idx * CARD_W
            draw_card_cell(c, card, x, y_cursor)

        y_cursor -= row_h

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
            print(f"  ✓  {name} ({card_info.get('rarity','?')}) → {row['日本語名']}")
        else:
            not_found.append(card_info)
            print(f"  ✗  {name} ({card_info.get('rarity','?')}) [card_number={card_info.get('card_number')}] — 翻訳データなし")

    print(f"\n翻訳データ: {len(sticker_cards)}/{len(cards_raw)} 枚")

    if not sticker_cards:
        print("出力するカードがありません。終了します。")
        sys.exit(1)

    # --- Step 3: PDF 生成 ---
    # reportlab で直接生成
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
