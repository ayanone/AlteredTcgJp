"""
コアロジックの動作確認スクリプト。
カード画像ファイルを引数に渡して実行する:
  python test_core.py <画像ファイルパス>
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv()

from app.config import GEMINI_API_KEY, CSV_PATH, UNIQUES_CSV_PATH, KEYWORDS_PATH
from app.services.card_recognizer import recognize_card, translate_card
from app.services.csv_manager import (
    find_translation, append_translation,
    find_unique_translation, append_unique_translation,
)

def main():
    if len(sys.argv) < 2:
        print("使い方: python test_core.py <画像ファイルパス>")
        sys.exit(1)

    image_path = sys.argv[1]
    if not os.path.exists(image_path):
        print(f"ファイルが見つかりません: {image_path}")
        sys.exit(1)

    if not GEMINI_API_KEY:
        print("GEMINI_API_KEY が設定されていません。.env を確認してください。")
        sys.exit(1)

    print(f"APIキー確認: {GEMINI_API_KEY[:8]}...")
    print(f"画像: {image_path}")
    print("-" * 40)

    with open(image_path, "rb") as f:
        image_bytes = f.read()

    # 1. カード認識
    print("① カード認識中...")
    card_info = recognize_card(GEMINI_API_KEY, image_bytes)
    if card_info is None:
        print("認識失敗")
        sys.exit(1)

    card_number = card_info.get("card_number", "")
    rarity = card_info.get("rarity", "")
    unique_number = card_info.get("unique_number")
    card_name = card_info.get("card_name", "")
    card_type = card_info.get("card_type", "")
    card_subtypes = card_info.get("card_subtypes", [])
    card_text = card_info.get("card_text", "")

    print(f"  カード番号:   {card_number}")
    print(f"  レアリティ:   {rarity}")
    if unique_number:
        print(f"  ユニーク番号: {unique_number}")
    print(f"  カード名:     {card_name}")
    print(f"  カードタイプ: {card_type} {card_subtypes}")
    print(f"  テキスト:     {card_text[:80]}...")
    print()

    is_unique = (rarity == "U")

    # 2. CSV検索
    print("② CSV検索中...")
    if is_unique:
        existing = find_unique_translation(UNIQUES_CSV_PATH, card_number, unique_number)
        csv_label = UNIQUES_CSV_PATH
    else:
        existing = find_translation(CSV_PATH, card_number, rarity)
        csv_label = CSV_PATH

    if existing:
        print(f"  CSVに翻訳あり: {existing['日本語名']}")
        print(f"  能力: {existing['能力'][:80]}...")
    else:
        print(f"  CSVに翻訳なし → 翻訳を生成します")
        print()

        # 3. 翻訳生成
        print("③ 翻訳生成中...")
        result = translate_card(
            GEMINI_API_KEY, card_name, card_text,
            csv_path=CSV_PATH, keywords_path=KEYWORDS_PATH,
            card_type=card_type, card_subtypes=card_subtypes,
        )
        if result is None:
            print("翻訳失敗")
            sys.exit(1)

        print(f"  日本語名: {result['name_jp']}")
        print(f"  能力:     {result['ability_jp']}")
        print()

        # 4. CSVに保存
        save = input(f"CSVに保存しますか？({csv_label}) [y/N]: ").strip().lower()
        if save == "y":
            if is_unique:
                append_unique_translation(
                    UNIQUES_CSV_PATH, card_number, unique_number,
                    result["name_jp"], result["ability_jp"],
                )
            else:
                append_translation(
                    CSV_PATH, card_number, rarity,
                    result["name_jp"], result["ability_jp"],
                )
            print(f"  保存しました: {csv_label}")

    print()
    print("テスト完了")

if __name__ == "__main__":
    main()
