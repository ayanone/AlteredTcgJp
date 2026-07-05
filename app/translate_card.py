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
        f"gemini-3.5-flash:generateContent?key={api_key}"
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


def _load_keywords(keywords_path):
    """
    keywords.csv を読み込み「英語 → (日本語, カテゴリ, 注釈文, 備考)」辞書を返す。
    旧3カラム形式（備考に注釈文が混在）にも後方互換で対応する。
    """
    import csv as _csv, os as _os
    if not keywords_path or not _os.path.exists(keywords_path):
        return {}
    result = {}
    with open(keywords_path, encoding="utf-8", newline="") as f:
        for row in _csv.DictReader(f):
            en = row.get("英語", "").strip()
            jp = row.get("日本語", "").strip()
            if not en or not jp:
                continue
            category = (row.get("カテゴリ") or "").strip()
            annotation = (row.get("注釈文") or "").strip()
            note = (row.get("備考") or "").strip()
            result[en] = (jp, category, annotation, note)
    return result


def translate_card(api_key, card_name, card_text, csv_path=None, keywords_path=None, card_type=None):
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
        categories = {}
        for en, (jp, cat, annotation, note) in keywords.items():
            categories.setdefault(cat or "その他", []).append((en, jp, annotation, note))

        lines = ["【必ず以下の訳語・表記ルールを使うこと（変更禁止）】", ""]
        lines += [
            "■ 表記ルール",
            "・状態キーワード（Fleeting/asleep/anchored等）: テキスト中では _日本語名_ とアンダースコアで囲み、文末に注釈文をつける。英語テキストにカッコ書きで注釈が続いていても、その注釈は出力しないこと。例: 「Fleeting. (Send me to Discard...)」→「_一過_（私がリザーブに送られるなら、代わりに捨て札にする。）」例: 「gain Anchored. (During Rest, ...)」→「_アンカー_を得る。（休息時、私はリザーブに送られず、代わりにアンカーを失う。）」",
            "・キーワード能力（Gigantic/Seasoned等）: 「日本語名（注釈文）」の形式で出力する。アンダースコアや句点は不要。例: 巨大（私はあなたの両方の探検隊に存在しているとみなす。）",
            "・キーワード処理（sabotage/resupply等）: 注釈文があれば、文末に注釈文をつける。英語テキストにカッコ書きで注釈が続いていても、その注釈は出力しないこと。例: 「Sabotage. (Discard up to ...)」→「サボタージュする。（リザーブのカード最大1枚を対象とし、それを捨て札にする。）」",
            "・記号（[ウラ]/[表]/[両面]/＜サポート＞/[捨て札]/[永続]/＜_達成済み_＞）: 括弧ごと固定表記を使う",
            "・カードタイプ（Character/Permanent/Spell/Hero）: 日本語のみ表記（英語名不要）。例: Character → キャラクター",
            "・カンマ区切りで通称を表しているカード名は、日本語のカード名では通称と名前の順を入れ替える。例: Leo, Relic Expert → 遺物の専門家、レオ",
            "・&区切りのカード名は通称ではないためそのままの順序で訳す。&は「と」と訳す。 例: Akesha & Taru → アケシャとタル",
            "・トークンを生成する個数が1個の場合は数を省略しない。例: create a ～ token -> ～トークン1個を生成する",
            "・生け贄に捧げる個数が1個の場合は数を省略しない。例: sacrefice a character -> キャラクター1体を生け贄に捧げる",
            "・youであるかどうかは自明ではないため、訳す際に省略しない。例: Discard your hand. -> あなたの手札を捨てる。 例: in your landmarks -> あなたのランドマークに",
            "・Iであるかどうかは自明ではないため、訳す際に省略しない。例: I gain 1 boost. -> 私は1ブーストを得る。 例: my expedition  -> 私の探検隊",
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
    if "Permanent" in card_type and keywords:
        super_types = card_type.split(" ")[:-1]
        annotations = []
        for super_type in super_types:
            if super_type in keywords:
                jp, cat, annotation, note = keywords[super_type]
                if annotation:
                    annotations.append(annotation)
        if annotations:
            permanent_rule = (
                "【パーマネント特殊タイプの注釈文ルール】\n"
                "ability_jp の先頭に以下の注釈文を付加すること:\n"
                + "\n".join(annotations) + "\n"
            )

    subtype_rule = ""
    if keywords:
        subtype_entries = [
            (en, jp) for en, (jp, cat, annotation, note) in keywords.items()
            if cat == "サブタイプ"
        ]
        if subtype_entries:
            examples_list = "、".join(f"{jp}/{en}" for en, jp in subtype_entries[:5])
            subtype_rule = (
                "【サブタイプの表記ルール】\n"
                f"能力テキスト中にサブタイプが登場する場合は「日本語名/英語名」の形式で表記すること（例: {examples_list}）。\n"
                "ただし「create ～ token」の間に登場するサブタイプは日本語名のみとし、英語名を付けないこと。\n"
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
