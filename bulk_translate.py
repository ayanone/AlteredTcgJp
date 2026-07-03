"""
CARDS/EN/*.json から未翻訳カードを一括翻訳して AlteredTcgJp.csv に追記するスクリプト。
1回のAPIコールで最大 BATCH_SIZE 件をまとめて翻訳（レート制限対策）。

使い方:
  python bulk_translate.py [--dry-run] [--limit N] [--set BTG,TBF,...]

オプション:
  --dry-run      翻訳APIを呼ばずに未翻訳カード一覧だけ表示
  --limit N      翻訳するカード数の上限
  --set X,Y,...  対象セットを限定（デフォルト: BTG,TBF,WFM,SKY,SDU,ROC）
  --batch-size N 1回のAPIコールで翻訳する件数（デフォルト: 5）
  --delay F      APIコール間隔(秒)（デフォルト: 13、5RPM制限内に収める）
"""

import json
import os
import re
import csv
import sys
import time
import argparse
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent))
from dotenv import load_dotenv
load_dotenv()

from app.config import GEMINI_API_KEY, CSV_PATH, KEYWORDS_PATH, ODT_REFERENCE_PATH
from app.services.card_recognizer import _call_gemini, _load_keywords, _load_odt_examples
from app.services.csv_manager import load_csv, get_year_month, FIELDNAMES, _RARITY_ORDER

# ────────────────────────────────────────────────────────────────
# 定数
# ────────────────────────────────────────────────────────────────
SET_DIR_MAP = {
    "BTG": "CORE",
    "TBF": "ALIZE",
    "WFM": "BISE",
    "SKY": "CYCLONE",
    "SDU": "DUSTER",
    "ROC": "EOLE",
}

PREFIX_YEAR_MONTH = {
    "BTG": "2024-09",
    "TBF": "2025-01",
    "WFM": "2025-05",
    "SKY": "2025-09",
    "SDU": "2026-02",
    "ROC": "2026-05",
}

ICON_MAP = {
    "{J}": "[両面]", "{j}": "[両面]",
    "{H}": "[表]",   "{h}": "[表]",
    "{R}": "[ウラ]", "{r}": "[ウラ]",
    "{D}": "[捨て札]",
    "{T}": "[消耗]",
    "{V}": "森",
    "{M}": "山",
    "{O}": "海",
    "{1}": "[1]", "{2}": "[2]", "{3}": "[3]", "{4}": "[4]",
    "{5}": "[5]", "{6}": "[6]", "{7}": "[7]", "{X}": "[X]",
}


# ────────────────────────────────────────────────────────────────
# テキスト正規化
# ────────────────────────────────────────────────────────────────
def _normalize_common(text: str) -> str:
    """共通の正規化処理（ノーブレークスペース、[[Keyword]]、#bold# 除去、半角スペース2つ→改行）"""
    text = text.replace("\xa0", " ")
    text = re.sub(r"\[\[(.+?)\]\]", r"\1", text)
    text = re.sub(r"#(.+?)#", r"\1", text)
    # 半角スペース2つ連続は改行を意味する
    text = re.sub(r"  +", "\n", text)
    return text


def normalize_effect(text: str) -> str:
    """MAIN_EFFECT の正規化"""
    if not text:
        return ""
    text = _normalize_common(text)
    for raw, repl in ICON_MAP.items():
        text = text.replace(raw, repl)
    return text.strip()


def normalize_echo_effect(text: str) -> str:
    """ECHO_EFFECT の正規化。アイコン種別によって3種類に分岐:
    - {D}: ＜サポート＞[捨て札]: 形式（手札捨てコストのサポート能力）
    - {I}: ＜サポート＞[永続] 形式（永続的に効果を発揮するサポート能力）
    - [[Completed]]: 達成済み状態トリガー（[[]]除去後にそのまま）
    """
    if not text:
        return ""
    text = _normalize_common(text)

    # {D} サポート能力
    if text.startswith("{D}") or text.lstrip().startswith("{D}"):
        text = text.replace("{D}", "[捨て札]")
        for raw, repl in ICON_MAP.items():
            if raw != "{D}":
                text = text.replace(raw, repl)
        text = re.sub(r'\s*\(Discard me from Reserve[^)]*\)', '', text)
        text = re.sub(r'^\[捨て札\]\s*:\s*', '', text)
        return f"＜サポート＞[捨て札]:{text.strip()}"

    # {I} WFM固有パッシブ予約能力 → [永続]prefix
    if text.startswith("{I}") or text.lstrip().startswith("{I}"):
        text = re.sub(r'^\{I\}\s*', '', text)
        for raw, repl in ICON_MAP.items():
            text = text.replace(raw, repl)
        return f"＜サポート＞[永続]{text.strip()}"

    # その他（[[Completed]]トリガー等）は共通アイコン変換のみ
    for raw, repl in ICON_MAP.items():
        text = text.replace(raw, repl)
    return text.strip()


# ────────────────────────────────────────────────────────────────
# カードデータ収集
# ────────────────────────────────────────────────────────────────
def collect_cards(target_sets: list[str]) -> list[dict]:
    cards = []
    for set_code in target_sets:
        dir_name = SET_DIR_MAP.get(set_code)
        if not dir_name:
            continue
        base = Path("CARDS/EN") / dir_name
        if not base.exists():
            print(f"[警告] ディレクトリが存在しません: {base}")
            continue
        for root, _, files in os.walk(base):
            for fn in sorted(files):
                if not fn.endswith(".json"):
                    continue
                try:
                    with open(os.path.join(root, fn), encoding="utf-8") as f:
                        data = json.load(f)
                    cn = data.get("collectorNumber", "")
                    m = re.match(r"^(.+)-([A-Z][A-Z]?)-EN$", cn)
                    if not m:
                        continue
                    card_number = m.group(1)
                    rarity = m.group(2)
                    if rarity == "T":
                        continue
                    name = data.get("name", "")
                    elements = data.get("elements", {})
                    raw_main = elements.get("MAIN_EFFECT", "")
                    raw_echo = elements.get("ECHO_EFFECT", "")
                    main_text = normalize_effect(raw_main)
                    echo_text = normalize_echo_effect(raw_echo) if raw_echo else ""
                    if main_text and echo_text:
                        effect = main_text + "\n" + echo_text
                    else:
                        effect = main_text or echo_text
                    faction = (data.get("mainFaction") or {}).get("name", "")
                    cards.append({
                        "card_number": card_number,
                        "rarity": rarity,
                        "name": name,
                        "faction": faction,
                        "effect": effect,
                    })
                except Exception as e:
                    print(f"[警告] {fn}: {e}")
    return cards


# ────────────────────────────────────────────────────────────────
# バッチ翻訳プロンプト構築
# ────────────────────────────────────────────────────────────────
def _build_batch_prompt(batch: list[tuple[str, str]], keywords_path: str, odt_path: str, csv_path: str) -> str:
    keywords = _load_keywords(keywords_path)
    keywords_text = ""
    if keywords:
        categories = {}
        for en, (jp, cat, annotation, note) in keywords.items():
            categories.setdefault(cat or "その他", []).append((en, jp, annotation, note))

        lines = ["【必ず以下の訳語・表記ルールを使うこと（変更禁止）】", ""]
        lines += [
            "■ 表記ルール",
            "・状態キーワード（Fleeting/asleep/anchored等）: テキスト中では _日本語名_ とアンダースコアで囲み、文末に注釈文をつける。英語テキストにカッコ書きで注釈が続いていても、その注釈は出力しないこと。例: 「Fleeting. (Send me to Discard...)」→「_一過_（私がリザーブに送られるなら、代わりに捨て札にする。）」例: 「gain Anchored. (During Rest, ...)」→「_アンカー_を得る。（休息時、私はリザーブに送られず、代わりにアンカーを失う。）」",
            "・キーワード能力（Gigantic/Seasoned等）: 「日本語名（注釈文）」の形式で出力する。アンダースコアや句点は不要。例: 巨大（私はあなたの両方の探検隊に存在しているとみなす。）",
            "・キーワード処理（sabotage/resupply等）: 注釈文があれば、文末に注釈文をつける。英語テキストにカッコ書きで注釈が続いていても、その注釈は出力しないこと。例: 「Sabotage. (Discard up to ...)」→「サボタージュする。（リザーブのカード最大1枚を対象とし、それを捨て札にする。）」",
            "・記号（[ウラ]/[表]/[両面]/＜サポート＞/[捨て札]/[永続]）: 括弧ごと固定表記を使う",
            "・カードタイプ（Character/Permanent/Spell/Hero）: 日本語のみ表記（英語名不要）。例: Character → キャラクター",
            "・二つ名を持つカード名は、日本語のカード名では二つ名と名前の順を入れ替える。例: Leo, Relic Expert → 遺物の専門家、レオ",
            "",
        ]
        for cat, entries in categories.items():
            lines.append(f"■ {cat}")
            for en, jp, annotation, note in entries:
                lines.append(f"  {en} → {jp}")
                if annotation:
                    lines.append(f"    注釈文: {annotation}")
                if note:
                    lines.append(f"    備考: {note}")
            lines.append("")
        keywords_text = "\n".join(lines)

    odt_text = _load_odt_examples(odt_path, max_samples=5)

    examples_text = ""
    if csv_path and os.path.exists(csv_path):
        try:
            with open(csv_path, encoding="utf-8", newline="") as f:
                rows = list(csv.DictReader(f))
            samples = [r for r in rows if r.get("能力", "").strip()][:5]
            if samples:
                lines = ["【参考：既存の翻訳スタイル（文体・表記を合わせること）】"]
                for r in samples:
                    lines.append(f"カード名: {r['日本語名']}")
                    lines.append(f"能力: {r['能力']}")
                    lines.append("")
                examples_text = "\n".join(lines)
        except Exception:
            pass

    # バッチカード一覧
    card_lines = []
    for i, (name, effect) in enumerate(batch, 1):
        card_lines.append(f"{i}. カード名: {name}")
        card_lines.append(f"   テキスト: {effect if effect else '（テキストなし）'}")
    cards_text = "\n".join(card_lines)

    return f"""以下の{len(batch)}枚のAltered TCGカードを日本語に翻訳してください。

{keywords_text}
{odt_text}
{examples_text}
【翻訳対象】
{cards_text}

以下のJSON配列形式で返してください。配列の順番は翻訳対象の番号順と一致させること。JSONのみ返してください。

[
  {{"name_jp": "日本語のカード名", "ability_jp": "日本語のカードテキスト"}},
  ...
]"""


# ────────────────────────────────────────────────────────────────
# バッチ翻訳実行
# ────────────────────────────────────────────────────────────────
def translate_batch(api_key: str, batch: list[tuple[str, str]],
                    keywords_path: str, odt_path: str, csv_path: str) -> list[dict] | None:
    prompt = _build_batch_prompt(batch, keywords_path, odt_path, csv_path)
    try:
        response = _call_gemini(api_key, prompt, max_retries=5)
        response = re.sub(r"^```[a-z]*\n?", "", response.strip())
        response = re.sub(r"\n?```$", "", response)
        results = json.loads(response)
        if isinstance(results, list) and len(results) == len(batch):
            return results
        print(f"  [警告] 返却件数不一致: 期待{len(batch)}件, 実際{len(results) if isinstance(results, list) else '不正'}")
        return None
    except Exception as e:
        print(f"  [エラー] バッチ翻訳失敗: {e}")
        return None


# ────────────────────────────────────────────────────────────────
# メイン
# ────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--set", dest="sets", default="BTG,TBF,WFM,SKY,SDU,ROC")
    parser.add_argument("--batch-size", type=int, default=5, help="1APIコールあたりの翻訳件数")
    parser.add_argument("--delay", type=float, default=13.0, help="APIコール間隔(秒) 5RPM制限=12秒以上")
    args = parser.parse_args()

    target_sets = [s.strip() for s in args.sets.split(",")]
    print(f"対象セット: {target_sets}")
    print(f"バッチサイズ: {args.batch_size}件/コール、間隔: {args.delay}秒")

    if not GEMINI_API_KEY and not args.dry_run:
        print("エラー: GEMINI_API_KEY が設定されていません。")
        sys.exit(1)

    existing = load_csv(CSV_PATH)
    rows = list(existing.values())
    print(f"既存CSV: {len(existing)}件")

    all_cards = collect_cards(target_sets)
    print(f"JSON総カード数（Token除く）: {len(all_cards)}件")

    missing = [c for c in all_cards if (c["card_number"], c["rarity"]) not in existing]
    print(f"未翻訳カード: {len(missing)}件")

    if not missing:
        print("未翻訳カードはありません。")
        return

    # ユニーク (name, effect) でまとめる
    unique_effects: dict[tuple, list] = {}
    for c in missing:
        key = (c["name"], c["effect"])
        unique_effects.setdefault(key, []).append((c["card_number"], c["rarity"], c.get("faction", "")))

    unique_list = list(unique_effects.items())
    if args.limit:
        unique_list = unique_list[:args.limit]

    print(f"ユニーク翻訳対象: {len(unique_list)}件 → {-(-len(unique_list)//args.batch_size)}バッチ")

    if args.dry_run:
        print("\n--- 未翻訳カード一覧（最初の30件）---")
        for (name, effect), variants in unique_list[:30]:
            print(f"  {name}: {effect[:60]}")
        return

    # 進捗ファイル: どこまで翻訳したか記録（再実行時に続きから）
    progress_file = Path("bulk_translate_progress.json")
    done_keys: set[tuple] = set()
    if progress_file.exists():
        with open(progress_file, encoding="utf-8") as f:
            done_list = json.load(f)
        done_keys = {tuple(k) for k in done_list}
        print(f"前回の進捗を読み込み: {len(done_keys)}件完了済み")
        # 完了済みをスキップ
        unique_list = [(k, v) for k, v in unique_list if k not in done_keys]
        print(f"残り翻訳対象: {len(unique_list)}件")

    new_rows = []
    translated_count = 0

    for batch_start in range(0, len(unique_list), args.batch_size):
        batch_items = unique_list[batch_start:batch_start + args.batch_size]
        batch_names_effects = [(name, effect) for (name, effect), _ in batch_items]
        batch_num = batch_start // args.batch_size + 1
        total_batches = -(-len(unique_list) // args.batch_size)

        print(f"\n[バッチ {batch_num}/{total_batches}] {[n for n, _ in batch_names_effects]}")

        results = translate_batch(GEMINI_API_KEY, batch_names_effects,
                                  KEYWORDS_PATH, ODT_REFERENCE_PATH, CSV_PATH)

        if results is None:
            print("  → スキップ（後で再実行してください）")
            time.sleep(args.delay)
            continue

        for i, ((name, effect), variants) in enumerate(batch_items):
            r = results[i]
            name_jp = r.get("name_jp", "")
            ability_jp = r.get("ability_jp", "")
            print(f"  {name} → {name_jp}")
            if ability_jp:
                print(f"    {ability_jp[:70]}")

            for card_number, rarity, faction_val in variants:
                year_month = get_year_month(CSV_PATH, card_number)
                if not year_month:
                    prefix = re.match(r"([A-Z]+)-", card_number)
                    if prefix:
                        year_month = PREFIX_YEAR_MONTH.get(prefix.group(1), "")
                new_rows.append({
                    "年月": year_month,
                    "カード番号": card_number,
                    "レアリティ": rarity,
                    "陣営": faction_val,
                    "英語名": name,
                    "日本語名": name_jp,
                    "能力": ability_jp,
                    "訳者コメント": "",
                })

        translated_count += len(batch_items)

        # 進捗ファイルを更新（成功したキーを追記）
        for (name, effect), _ in batch_items:
            done_keys.add((name, effect))
        with open(progress_file, "w", encoding="utf-8") as f:
            json.dump([list(k) for k in done_keys], f, ensure_ascii=False)

        # 10バッチごとにCSVに中間保存
        if batch_num % 10 == 0:
            _save_csv(rows + new_rows)
            print(f"  ★ 中間保存: 現在 {len(rows) + len(new_rows)}件")

        if batch_start + args.batch_size < len(unique_list):
            time.sleep(args.delay)

    if not new_rows:
        print("\n追加する翻訳がありませんでした。")
        return

    print(f"\n{len(new_rows)}件の翻訳をCSVに保存中...")
    _save_csv(rows + new_rows)
    print(f"完了: CSV合計 {len(rows) + len(new_rows)}件")


def _save_csv(all_rows: list[dict]):
    def sort_key(r):
        num = r["カード番号"]
        m = re.match(r"([A-Z]+-)+(\d+)", num)
        prefix = re.sub(r"\d+", "", num)
        number = int(m.group(2)) if m else 0
        return (r["年月"], prefix, number, _RARITY_ORDER.get(r["レアリティ"], 99))

    all_rows = sorted(all_rows, key=sort_key)
    with open(CSV_PATH, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rows)


if __name__ == "__main__":
    main()
