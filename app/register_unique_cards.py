"""
Usage: python register_unique_cards.py <image_path>

画像内のユニークカードを Gemini API で認識し、英語名と能力テキストを一括翻訳して
uniques.csv に登録する。
"""
import sys
import os
import re
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from app.config import GEMINI_API_KEY, CSV_PATH, UNIQUES_CSV_PATH, KEYWORDS_PATH
from app.csv_manager import load_csv, load_uniques, append_unique_translation, lookup_csv_row
from app.export_jp_translation import recognize_all_cards
from app.translate_card import translate_card

TRANSLATE_PROMPT_TEMPLATE = """\
以下はAltered TCGのユニークカードの英語能力テキストリストです。
各カードの能力テキスト（card_text）を日本語に翻訳してください。

翻訳ルール:
- AlteredTCGの公式日本語版の訳文スタイルに合わせて翻訳する。
- card_text 内の {{H}} {{R}} {{J}} {{T}} {{D}} [1] [2] などの記号はそのまま残す。
- [Support] は ＜サポート＞ に、[Completed] は ＜達成済み＞ に置き換える。
- card_text が空の場合は ability_jp も空文字にする。

入力JSON:
{input_json}

以下の形式のJSON配列のみを返してください（マークダウン不要）:
[
  {{
    "index": 0,
    "ability_jp": "翻訳した日本語能力テキスト"
  }},
  ...
]
"""


def _pad_card_number(card_number: str) -> str:
    """カード番号の数字部分を3桁に0埋めする。例: SKY-22 → SKY-022"""
    m = re.match(r"^([A-Za-z]+)-(\d+)$", card_number)
    if not m:
        return card_number
    return f"{m.group(1)}-{m.group(2).zfill(3)}"


def _lookup_csv_row(csv_data: dict, card_number: str) -> dict | None:
    """カード番号でCSVを検索して最初にヒットした行を返す"""
    for (num, _rarity), row in csv_data.items():
        if num == card_number:
            return row
    return None


def main():
    if len(sys.argv) < 2:
        print("Usage: python register_unique_cards.py <image_path>")
        sys.exit(1)

    image_path = sys.argv[1]
    if not os.path.exists(image_path):
        print(f"エラー: ファイルが見つかりません: {image_path}")
        sys.exit(1)

    if not GEMINI_API_KEY:
        print("エラー: GEMINI_API_KEY が設定されていません。.env ファイルを確認してください。")
        sys.exit(1)

    # --- Step 1: OCR ---
    print(f"画像を認識中: {image_path}")
    cards_raw = recognize_all_cards(GEMINI_API_KEY, image_path)
    print(f"  → {len(cards_raw)} 枚のカードを検出")

    csv_data = load_csv(CSV_PATH)
    uniques_data = load_uniques(UNIQUES_CSV_PATH) if os.path.exists(UNIQUES_CSV_PATH) else {}

    # --- Step 2: ユニークカードの絞り込み・既登録チェック ---
    to_translate = []   # 翻訳が必要なカード

    for card in cards_raw:
        rarity = (card.get("rarity") or card.get("rarity_ocr") or "").strip()
        if rarity != "U":
            continue

        raw_number = (card.get("card_number") or "").strip()
        unique_number = str(card.get("unique_number") or "").strip()

        if not raw_number or not unique_number:
            print(f"  スキップ（カード番号またはユニーク番号が読み取れませんでした）: {card.get('card_name')}")
            continue

        card_number = _pad_card_number(raw_number)
        if card_number != raw_number:
            print(f"  カード番号を補正: {raw_number} → {card_number}")

        if (card_number, unique_number) in uniques_data:
            print(f"  スキップ（登録済み）: {card_number}-U-{unique_number} {card.get('card_name')}")
            continue

        csv_row = _lookup_csv_row(csv_data, card_number)
        year_month = csv_row.get("年月", "") if csv_row else ""
        name_jp = csv_row.get("日本語名", "") if csv_row else ""

        entry = {
            "index": len(to_translate),
            "card_number": card_number,
            "unique_number": unique_number,
            "card_name": (card.get("card_name") or "").strip(),
            "card_text": (card.get("card_text") or "").strip(),
            "name_jp": name_jp,
            "year_month": year_month,
        }
        to_translate.append(entry)

    if not to_translate:
        print("登録対象のユニークカードがありませんでした。")
        return

    print(f"\n{len(to_translate)} 枚を翻訳中...")
    csv_data = load_csv(CSV_PATH)
    uniques_data = load_uniques(UNIQUES_CSV_PATH)

    registered = 0
    skipped = 0

    for card in to_translate:
        raw_number = (card.get("card_number") or "").strip()
        unique_number = str(card.get("unique_number") or "").strip()
        card_name = (card.get("card_name") or "").strip()
        card_text = (card.get("card_text") or "").strip()
        card_type = card.get("card_type") or ""
        super_types = card.get("super_types") or []
        card_subtypes = card.get("card_subtypes") or []

        if not raw_number or not unique_number:
            print(f"  スキップ（カード番号またはユニーク番号が読み取れませんでした）: {card_name}")
            skipped += 1
            continue

        # 0埋め正規化
        card_number = _pad_card_number(raw_number)
        if card_number != raw_number:
            print(f"  カード番号を補正: {raw_number} → {card_number}")

        # 既登録チェック
        if (card_number, unique_number) in uniques_data:
            print(f"  スキップ（登録済み）: {card_number}-U-{unique_number} {card_name}")
            skipped += 1
            continue

        print(f"  処理中: {card_number}-U-{unique_number} {card_name}")

        # AlteredTcgJp.csv から日本語カード名・年月を取得
        csv_row = lookup_csv_row(csv_data, card_number)
        name_jp = csv_row.get("日本語名") or None if csv_row else None
        year_month = csv_row.get("年月") or None if csv_row else None
        if name_jp:
            print(f"    カード名 (CSV): {name_jp}  年月: {year_month}")
        else:
            print(f"    カード名がCSVに見つかりません。翻訳します...")

        # 能力テキストを翻訳
        translation = translate_card(
            GEMINI_API_KEY,
            card_name,
            card_text,
            csv_path=CSV_PATH,
            keywords_path=KEYWORDS_PATH if os.path.exists(KEYWORDS_PATH) else None,
            super_types=super_types,
            card_type=card_type,
            card_subtypes=card_subtypes,
        )

        if not translation:
            print(f"    警告: 翻訳に失敗しました。スキップします。")
            skipped += 1
            continue

        if not name_jp:
            name_jp = translation.get("name_jp") or card_name
            print(f"    カード名 (翻訳): {name_jp}")

        ability_jp = translation.get("ability_jp") or ""
        # [Support] が翻訳されずに残っている場合は ＜サポート＞ に置換
        ability_jp = ability_jp.replace("[Support]", "＜サポート＞")

        append_unique_translation(
            UNIQUES_CSV_PATH,
            year_month=year_month,
            name_jp=name_jp,
            ability_jp=ability_jp,
            card=card,
        )
        print(f"    登録完了: {name_jp}")
        registered += 1

    print(f"\n完了: {registered} 枚登録, {skipped} 枚スキップ")
    print(f"登録先: {os.path.abspath(UNIQUES_CSV_PATH)}")


if __name__ == "__main__":
    main()
