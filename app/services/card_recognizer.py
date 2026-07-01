import base64
import json
import re
import urllib.request
import urllib.error


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
            print(f"HTTP {e.code}: {body}")
            if e.code == 429:
                wait = 2 ** attempt
                print(f"レート制限 (429)。{wait}秒後にリトライ... ({attempt + 1}/{max_retries})")
                time.sleep(wait)
            else:
                raise
    raise RuntimeError("Gemini API: リトライ上限に達しました")


def recognize_card(api_key, image_bytes):
    """
    カード画像からカード番号・レアリティ・カード名・テキストを抽出する。
    Returns:
        dict with keys: card_number, rarity, card_name, card_text
        失敗時は None
    """
    prompt = """この画像はAltered TCGのカードです。以下の情報をJSON形式で返してください。

【カード番号・レアリティの読み取り】
カードの下部にある識別文字列から読み取ってください。
レアリティはC（コモン）、R（レア）、F（色違い）、E（エグザルテッド）、H（ヒーロー）、U（ユニーク）です。

通常カードの形式: 「BTG-052-R」→ card_number=BTG-052, rarity=R, unique_number=null
ユニークカードの形式: 「ROC-102-U-18245」→ card_number=ROC-102, rarity=U, unique_number=18245

【カードテキストの記号変換ルール（必ず適用すること）】
以下のアイコン・記号を指定の文字列に置き換えてテキストを出力してください。

・テキスト欄に手のアイコンがある → [Recto] と書く
・テキスト欄にカードが光っているようなアイコン（予約アイコン）がある → [Turbo] と書く
・テキスト欄に右向き矢印のアイコンがある → [Dual] と書く
・テキスト欄に丸で囲まれた数字（①②③など）がある → [1][2][3] のように角括弧付きの数字に置き換える
・テキスト欄にカードの上にXが書いてあるアイコン（破棄アイコン）がある → [Discard] と書く
・カードの下部の色が変わっている領域（サポート欄）に書かれている能力 → 先頭に [Support] と付けて続ける

【出力形式】
{
  "card_number": "ROC-102のように接頭辞-番号の形式",
  "rarity": "U など1文字",
  "unique_number": "18245のような数字（ユニーク以外はnull）",
  "card_name": "カード上部に書かれた英語のカード名",
  "card_type": "Character / Permanent / Spell / Hero のいずれか",
  "card_subtypes": ["Gear", "Mage" などサブタイプのリスト。なければ空リスト],
  "card_text": "上記ルールを適用したカードの能力テキスト全文（英語のまま）"
}

JSONのみ返してください。マークダウンのコードブロックは不要です。"""

    try:
        response = _call_gemini(api_key, prompt, image_bytes)
        response = response.strip()
        # ```json ... ``` 形式で返ってきた場合に対応
        response = re.sub(r"^```[a-z]*\n?", "", response)
        response = re.sub(r"\n?```$", "", response)
        data = json.loads(response)
        return data
    except Exception as e:
        print(f"recognize_card error: {e}")
        return None


def _load_keywords(keywords_path):
    """keywords.csv を読み込み「英語 → (日本語, 備考)」辞書を返す"""
    import csv as _csv, os as _os
    if not keywords_path or not _os.path.exists(keywords_path):
        return {}
    result = {}
    with open(keywords_path, encoding="utf-8", newline="") as f:
        for row in _csv.DictReader(f):
            en = row.get("英語", "").strip()
            jp = row.get("日本語", "").strip()
            note = row.get("備考", "").strip()
            if en and jp:
                result[en] = (jp, note)
    return result


def translate_card(api_key, card_name, card_text, csv_path=None, keywords_path=None,
                   card_type=None, card_subtypes=None):
    """
    カード名とテキストを日本語に翻訳する。
    csv_path      : 既存翻訳CSVのパス（スタイル参考例に使う）
    keywords_path : keywords.csvのパス（訳語固定辞書）
    Returns:
        dict with keys: name_jp, ability_jp
        失敗時は None
    """
    # キーワード辞書
    keywords = _load_keywords(keywords_path)
    keywords_text = ""
    if keywords:
        # カテゴリ別に分類して出力
        categories = {}
        for en, (jp, note) in keywords.items():
            # 備考の先頭単語をカテゴリとして使う（「状態（...）」→「状態」）
            cat = note.split("（")[0].split("。")[0].strip() if note else "その他"
            categories.setdefault(cat, []).append((en, jp, note))

        lines = ["【必ず以下の訳語・表記ルールを使うこと（変更禁止）】", ""]

        # 状態キーワードの表記ルールを最初に明示
        lines += [
            "■ 表記ルール",
            "・状態（Fleeting/asleep/anchored等）: テキスト中では _日本語名_ とアンダースコアで囲む",
            "・キーワード能力（Gigantic/Seasoned等）: 「_日本語名_。（注釈文）」の形式で出力する",
            "・記号（[ウラ]/[表]/[両面]/＜サポート＞/[捨て札]）: 括弧ごと固定表記を使う",
            "",
        ]

        for cat, entries in categories.items():
            lines.append(f"■ {cat}")
            for en, jp, note in entries:
                # 注釈文がある場合は別行で示す
                if "（" in note:
                    annotation = note[note.index("（"):]
                    lines.append(f"  {en} → {jp}")
                    lines.append(f"    注釈文: {annotation}")
                else:
                    lines.append(f"  {en} → {jp}")
            lines.append("")

        keywords_text = "\n".join(lines)

    # 既存CSVから参考例を最大5件取得
    examples_text = ""
    if csv_path:
        try:
            import csv as _csv, os as _os
            if _os.path.exists(csv_path):
                with open(csv_path, encoding="utf-8", newline="") as f:
                    rows = list(_csv.DictReader(f))
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

    # パーマネントの特殊タイプ注釈文ルール
    permanent_rule = ""
    if card_type == "Permanent" and card_subtypes and keywords:
        annotations = []
        for subtype in card_subtypes:
            if subtype in keywords:
                jp, note = keywords[subtype]
                if "（" in note:
                    annotation = note[note.index("（"):]
                    annotations.append(f"_{jp}_。{annotation}")
                else:
                    annotations.append(f"_{jp}_。")
        if annotations:
            permanent_rule = (
                "【パーマネント特殊タイプの注釈文ルール】\n"
                "ability_jp の先頭に以下の注釈文を改行なしで付加すること:\n"
                + "\n".join(annotations) + "\n"
            )

    # サブタイプ表記ルール（keywords から日本語名を取得）
    subtype_rule = ""
    if keywords:
        subtype_entries = [
            (en, jp) for en, (jp, note) in keywords.items()
            if "サブタイプ" in note
        ]
        if subtype_entries:
            examples_list = "、".join(f"{jp}/{en}" for en, jp in subtype_entries[:5])
            subtype_rule = (
                "【サブタイプの表記ルール】\n"
                f"能力テキスト中にサブタイプが登場する場合は「日本語名/英語名」の形式で表記すること（例: {examples_list}）。\n"
                "ただし「create ～ Token」の間に登場するサブタイプは日本語名のみとし、英語名を付けないこと。\n"
            )

    prompt = f"""以下はAltered TCGのカードの情報です。日本語に翻訳してください。

{keywords_text}

{permanent_rule}
{subtype_rule}
{examples_text}
【翻訳対象】
カード名: {card_name}
テキスト: {card_text}

以下のJSON形式で返してください。JSONのみ返してください。

{{
  "name_jp": "日本語のカード名",
  "ability_jp": "日本語のカードテキスト"
}}"""

    try:
        response = _call_gemini(api_key, prompt)
        response = re.sub(r"^```[a-z]*\n?", "", response)
        response = re.sub(r"\n?```$", "", response)
        return json.loads(response)
    except Exception as e:
        print(f"translate_card error: {e}")
        return None
