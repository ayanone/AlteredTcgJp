import csv
import os
import re


FIELDNAMES = ["年月","カード番号","レアリティ","陣営","英語名","カードタイプ","サブタイプ","手札コスト","リザーブコスト","森","山","海","英語能力","日本語名","能力","訳者コメント"]
UNIQUE_FIELDNAMES = ["年月","カード番号","レアリティ","ユニーク番号","陣営","英語名","カードタイプ","サブタイプ","手札コスト","リザーブコスト","森","山","海","英語能力","日本語名","能力","訳者コメント"]

# レアリティのソート順
_RARITY_ORDER = {"H": 0, "C": 1, "R": 2, "F": 3, "E": 4, "U": 5, "T": 6}


def _sort_key(row):
    """CSVのソートキー: 年月昇順 → カード番号昇順 → レアリティ順(H→C→R→F→E→U→T)"""
    card_num = row["カード番号"]
    m = re.match(r"([A-Za-z]+)-(\d+)", card_num)
    if m:
        prefix, num = m.group(1), int(m.group(2))
    else:
        prefix, num = card_num, 0
    rarity_rank = _RARITY_ORDER.get(row["レアリティ"], 99)
    return (row["年月"], prefix, num, rarity_rank)


def load_csv(csv_path):
    """CSVを読み込み、(カード番号, レアリティ) → row の辞書を返す"""
    data = {}
    if not os.path.exists(csv_path):
        return data
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            key = (row["カード番号"], row["レアリティ"])
            data[key] = row
    return data


def _load_prefix_to_year_month(csv_path):
    """既存CSVからカードセットプレフィックス → 年月 の対応表を作る"""
    mapping = {}
    if not os.path.exists(csv_path):
        return mapping
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            card_num = row.get("カード番号", "")
            year_month = row.get("年月", "").strip()
            m = re.match(r"([A-Za-z]+)-\d+", card_num)
            if m and year_month:
                prefix = m.group(1)
                mapping.setdefault(prefix, year_month)
    return mapping


def get_year_month(csv_path, card_number):
    """カード番号のプレフィックスから年月を返す。不明なら空文字"""
    m = re.match(r"([A-Za-z]+)-\d+", card_number)
    if not m:
        return ""
    prefix = m.group(1)
    mapping = _load_prefix_to_year_month(csv_path)
    return mapping.get(prefix, "")


def find_translation(csv_path, card_number, rarity):
    """カード番号とレアリティで翻訳を検索する。見つからなければ None を返す"""
    return load_csv(csv_path).get((card_number, rarity))


def find_translation_by_name(csv_path, card_name_en, rarity=None, faction=None):
    """
    英語名でカードを検索する。
    rarity と faction が指定された場合はそれで絞り込む。
    複数ヒットする場合は最初の1件を返す。見つからなければ None を返す。
    """
    rows = load_csv(csv_path).values()
    candidates = [r for r in rows if r.get("英語名", "").lower() == card_name_en.lower()]
    if not candidates:
        return None
    if rarity:
        filtered = [r for r in candidates if r["レアリティ"] == rarity]
        if filtered:
            candidates = filtered
    if faction:
        filtered = [r for r in candidates if r.get("陣営", "") == faction]
        if filtered:
            candidates = filtered
    return candidates[0] if candidates else None


def _rewrite_sorted(csv_path, rows):
    """rowsをソートしてCSVを全書き換えする"""
    rows_sorted = sorted(rows, key=_sort_key)
    with open(csv_path, encoding="utf-8-sig", newline="", mode="w") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows_sorted)


# ──────────────────────────────────────────────
# ユニークカード専用 (uniques.csv)
# キー: (カード番号, ユニーク番号)
# ──────────────────────────────────────────────

def _unique_sort_key(row):
    card_num = row["カード番号"]
    m = re.match(r"([A-Za-z]+)-(\d+)", card_num)
    prefix, num = (m.group(1), int(m.group(2))) if m else (card_num, 0)
    unique_num = int(row.get("ユニーク番号", 0) or 0)
    return (row["年月"], prefix, num, unique_num)


def load_uniques(csv_path):
    """uniques.csv を読み込み、(カード番号, ユニーク番号) → row の辞書を返す"""
    data = {}
    if not os.path.exists(csv_path):
        return data
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            key = (row["カード番号"], row["ユニーク番号"])
            data[key] = row
    return data


def find_unique_translation(csv_path, card_number, unique_number):
    """ユニークカードの翻訳を検索する。見つからなければ None を返す"""
    return load_uniques(csv_path).get((card_number, str(unique_number)))


def append_unique_translation(csv_path, year_month, name_jp, ability_jp, card):
    """ユニークカードの翻訳を uniques.csv に追加してソート保存する"""
    existing = load_uniques(csv_path)
    key = (card["card_number"], str(card["unique_number"]))
    if key in existing:
        return

    new_row = {
        "年月": year_month,
        "カード番号": card["card_number"],
        "レアリティ": "U",
        "ユニーク番号": str(card["unique_number"]),
        "陣営": card["faction"],
        "英語名": card["card_name"],
        "カードタイプ": card["card_type"],
        "サブタイプ": "/".join(card["card_subtypes"]),
        "手札コスト": card["main_cost"],
        "リザーブコスト": card["recall_cost"],
        "森": card["forest"],
        "山": card["mountain"],
        "海": card["ocean"],
        "英語能力": card["card_text"],
        "日本語名": name_jp,
        "能力": ability_jp,
        "訳者コメント": "",
    }
    all_rows = list(existing.values()) + [new_row]
    rows_sorted = sorted(all_rows, key=_unique_sort_key)

    with open(csv_path, encoding="utf-8-sig", newline="", mode="w") as f:
        writer = csv.DictWriter(f, fieldnames=UNIQUE_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows_sorted)


def append_translation(csv_path, card_number, rarity, name_jp, ability_jp, comment="",
                       faction="", name_en=""):
    """新しい翻訳をCSVに追加し、ソート順を維持して保存する"""
    existing = load_csv(csv_path)
    key = (card_number, rarity)
    if key in existing:
        return

    year_month = get_year_month(csv_path, card_number)

    new_row = {
        "年月": year_month,
        "カード番号": card_number,
        "レアリティ": rarity,
        "陣営": faction,
        "英語名": name_en,
        "日本語名": name_jp,
        "能力": ability_jp,
        "訳者コメント": comment,
    }

    all_rows = list(existing.values()) + [new_row]
    _rewrite_sorted(csv_path, all_rows)
