"""
Usage: python export_jp_translation.py <image_path> [output_dir]

画像内の全カードをGemini APIで認識し、AlteredTcgJp.csv の翻訳データを使って
和訳シールの docx + PDF を出力する。
"""
import sys
import os
import json
import re
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

from app.config import GEMINI_API_KEY, CSV_PATH, UNIQUES_CSV_PATH, OUTPUT_DIR
from app.services.csv_manager import load_csv, load_uniques
from app.services.card_recognizer import _call_gemini
from app.services.docx_generator import generate_sticker_docx, get_output_path


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
            if rarity:
                filtered = [r for r in candidates if r["レアリティ"] == rarity]
                if filtered:
                    candidates = filtered
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
    reportlab で和訳シール PDF を直接生成する。
    A4 縦・3カラム・游ゴシック Light 6pt。
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, FrameBreak,
        NextPageTemplate, Frame, PageTemplate,
    )
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_LEFT
    import os, re

    # 游ゴシック Light を登録
    font_path = r"C:\Windows\Fonts\YuGothL.ttc"
    if not os.path.exists(font_path):
        font_path = r"C:\Windows\Fonts\meiryo.ttc"
    pdfmetrics.registerFont(TTFont("YuGothL", font_path))

    page_w, page_h = A4
    margin = 10 * mm
    col_gap = 5 * mm
    n_cols = 3
    col_w = (page_w - 2 * margin - (n_cols - 1) * col_gap) / n_cols
    frame_h = page_h - 2 * margin

    frames = [
        Frame(margin + i * (col_w + col_gap), margin, col_w, frame_h,
              leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
        for i in range(n_cols)
    ]
    template = PageTemplate(id="main", frames=frames)
    doc = SimpleDocTemplate(pdf_path, pagesize=A4,
                            leftMargin=margin, rightMargin=margin,
                            topMargin=margin, bottomMargin=margin)
    doc.addPageTemplates([template])

    # スタイル定義
    FONT_SIZE = 6
    HEADER_SIZE = 6.5
    header_style = ParagraphStyle(
        "header", fontName="YuGothL", fontSize=HEADER_SIZE,
        leading=HEADER_SIZE * 1.3, spaceAfter=0, spaceBefore=1 * mm,
        textColor="#222222", alignment=TA_LEFT,
    )
    body_style = ParagraphStyle(
        "body", fontName="YuGothL", fontSize=FONT_SIZE,
        leading=FONT_SIZE * 1.4, spaceAfter=2 * mm, spaceBefore=0,
        alignment=TA_LEFT,
    )

    def esc(text):
        """reportlab XML エスケープ + アンダースコア下線変換"""
        text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        # _xxx_ → <u>xxx</u>
        text = re.sub(r"_([^_]+)_", r"<u>\1</u>", text)
        return text

    story = []
    for card in sticker_cards:
        header_text = f"{card['card_number']}-{card['rarity']} {card['name_jp']}"
        story.append(Paragraph(esc(header_text), header_style))

        ability = card["ability_jp"]
        if ability:
            lines = ability.splitlines()
            body_text = "<br/>".join(esc(l) for l in lines)
            story.append(Paragraph(body_text, body_style))
        else:
            story.append(Spacer(1, 2 * mm))

    doc.build(story)
    return pdf_path


def docx_to_pdf(docx_path):
    """docxをPDFに変換する（Word COM）。失敗した場合はFalseを返す。"""
    pdf_path = str(docx_path).replace(".docx", ".pdf")
    try:
        from docx2pdf import convert
        convert(str(docx_path), pdf_path)
        return pdf_path
    except ImportError:
        pass
    except Exception as e:
        print(f"  [Word COM] PDF変換に失敗: {e}")
    return None


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

    # --- Step 3: docx 生成 ---
    os.makedirs(output_dir, exist_ok=True)
    docx_path = get_output_path(output_dir)
    generate_sticker_docx(docx_path, sticker_cards)
    print(f"Word ファイルを出力: {docx_path}")

    # --- Step 4: PDF 生成 ---
    pdf_path = str(docx_path).replace(".docx", ".pdf")
    # まず Word COM で変換を試みる
    converted = docx_to_pdf(docx_path)
    if converted:
        print(f"PDF を出力 (Word): {pdf_path}")
    else:
        # reportlab で直接生成
        try:
            generate_pdf_direct(sticker_cards, pdf_path)
            print(f"PDF を出力: {pdf_path}")
        except Exception as e:
            print(f"PDF 生成エラー: {e}")
            print("Word ファイルを手動で PDF に変換してください。")

    if not_found:
        print(f"\n翻訳データが見つからなかったカード ({len(not_found)} 枚):")
        for c in not_found:
            print(f"  {c.get('card_name','?')} / {c.get('rarity','?')} / {c.get('faction','?')}")


if __name__ == "__main__":
    main()
