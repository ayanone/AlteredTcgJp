"""
Usage: python register_unique_cards.py <image_path>

画像内のユニークカードを Gemini API で認識し、
AlteredTcgJp.csv の日本語カード名を使いながら能力テキストを翻訳して
uniques.csv に登録する。
"""
import sys
import os
import json
import re
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from app.config import GEMINI_API_KEY, CSV_PATH, UNIQUES_CSV_PATH, KEYWORDS_PATH
from app.csv_manager import load_csv, load_uniques, append_unique_translation
from app.card_recognizer import _call_gemini, translate_card


RECOGNIZE_PROMPT = """この画像にはAltered TCGのユニークカードが複数枚含まれています。
画像に写っているユニークカードをすべてリストアップし、各カードについて以下の情報をJSON配列で返してください。

【カード番号・ユニーク番号の読み取り】
カード下部の識別文字列（例: BTG-029-U-2080）から読み取ってください。
- card_number: 接頭辞-番号の部分（例: BTG-029）
- unique_number: 末尾の個体番号（例: 2080）

番号の桁数は必ず原文のまま読み取ってください（0埋めせず、見えた通りに）。

【カードテキストの記号変換ルール（必ず適用すること）】
以下のアイコン・記号を指定の文字列に置き換えてテキストを出力してください。

・テキスト欄に手のアイコンがある → [Recto] と書く
・テキスト欄にカードが光っているようなアイコン（予約アイコン）がある → [Turbo] と書く
・テキスト欄に右向き矢印のアイコンがある → [Dual] と書く
・テキスト欄に丸で囲まれた数字（①②③など）がある → [1][2][3] のように角括弧付きの数字に置き換える
・テキスト欄にカードの上にXが書いてあるアイコン（破棄アイコン）がある → [Discard] と書く
・サポート能力欄の識別方法と読み取り方:
  カード下部のテキスト欄は2つの領域に分かれています。
  上側の領域（MAIN_EFFECT）: 背景が透明でカードイラストが透けて見える。
  下側の領域（ECHO_EFFECT / サポート能力）: 背景がカード右上の旗マークと同じ陣営カラーで単一色に塗りつぶされている。
    （Axiom=茶色、Bravos=赤、Lyra=ピンク、Muna=緑、Ordis=青、Yzmir=紫、中立=グレー）
  この塗りつぶし背景の領域に書かれているテキストがサポート能力です。
  サポート能力が存在する場合は、そのテキストの先頭に [Support] を付けて card_text に続けてください。

【キャラクターのスタッツについて】
Altered のキャラクターには必ず3つのスタッツがあります（Mountain/Forest/Water の順）。
カードテキスト中に「1/1 Soldier token」のように見える場合でも、実際は「1/1/1」の3値です。
トークンを生成する効果（"Create a ... token"）では、必ずスタッツを3値で読み取ってください。

【出力形式】
[
  {
    "card_number": "BTG-029（見えた通り、0埋め不要）",
    "unique_number": "2080",
    "card_name": "カード上部の英語カード名",
    "faction": "Axiom / Bravos / Lyra / Muna / Ordis / Yzmir（旗マークの色から判定: 茶=Axiom, 赤=Bravos, ピンク=Lyra, 緑=Muna, 青=Ordis, 紫=Yzmir）",
    "card_type": "Character / Permanent / Spell / Hero のいずれか",
    "super_types": ["Token", "Expedition", "Landmark" の特殊タイプ。なければ空リスト],
    "card_subtypes": ["Mage", "Plant" などサブタイプ。なければ空リスト],
    "card_text": "上記ルールを適用したカードの能力テキスト全文（英語のまま）"
  }
]

JSON配列のみ返してください。マークダウンのコードブロックは不要です。"""


def _pad_card_number(card_number: str) -> str:
    """
    カード番号の数字部分を3桁に0埋めする。
    例: SKY-22 → SKY-022, BTG-029 → BTG-029（変化なし）
    """
    m = re.match(r"^([A-Za-z]+)-(\d+)$", card_number)
    if not m:
        return card_number
    prefix, num = m.group(1), m.group(2)
    return f"{prefix}-{num.zfill(3)}"


def recognize_all_unique_cards(api_key: str, image_path: str) -> list:
    """画像内のユニークカードをすべて認識してリストで返す"""
    with open(image_path, "rb") as f:
        image_bytes = f.read()

    ext = Path(image_path).suffix.lower().lstrip(".")
    mime_map = {
        "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "png": "image/png", "webp": "image/webp", "gif": "image/gif",
    }
    mime = mime_map.get(ext, "image/jpeg")

    response = _call_gemini(api_key, RECOGNIZE_PROMPT, image_bytes, mime)
    response = response.strip()
    response = re.sub(r"^```[a-z]*\n?", "", response)
    response = re.sub(r"\n?```$", "", response)
    return json.loads(response)


def lookup_csv_row(csv_data: dict, card_number: str) -> dict | None:
    """
    カード番号でCSVを検索して最初にヒットした行を返す（複数レアリティがある場合は最初の1件）。
    日本語名・年月の取得に使う。
    """
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

    print(f"画像を認識中: {image_path}")
    cards = recognize_all_unique_cards(GEMINI_API_KEY, image_path)
    print(f"  → {len(cards)} 枚のユニークカードを検出")

    csv_data = load_csv(CSV_PATH)
    uniques_data = load_uniques(UNIQUES_CSV_PATH)

    registered = 0
    skipped = 0

    for card in cards:
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
            card_number=card_number,
            unique_number=unique_number,
            name_jp=name_jp,
            ability_jp=ability_jp,
            year_month=year_month,
        )
        print(f"    登録完了: {name_jp}")
        registered += 1

    print(f"\n完了: {registered} 枚登録, {skipped} 枚スキップ")
    print(f"登録先: {os.path.abspath(UNIQUES_CSV_PATH)}")


if __name__ == "__main__":
    main()
