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


def recognize_card(api_key, image_bytes):
    """
    カード画像からカード番号・レアリティ・カード名・テキストを抽出する。
    カード番号が見えない場合はカード名・宝石色・旗色からカードを特定する。
    Returns:
        dict with keys: card_number, rarity, card_name, card_text, faction
        失敗時は None
    """
    prompt = """この画像はAltered TCGのカードです。以下の情報をJSON形式で返してください。

【カード番号・レアリティの読み取り】
カードの下部にある識別文字列から読み取ってください。
レアリティはC（コモン）、R（レア）、F（色違い）、E（エグザルテッド）、H（ヒーロー）、U（ユニーク）、T（トークン）です。

通常カードの形式: 「BTG-052-R」→ card_number=BTG-052, rarity=R, unique_number=null
ユニークカードの形式: 「ROC-102-U-18245」→ card_number=ROC-102, rarity=U, unique_number=18245

カード番号が読み取れない場合は card_number=nullとし、
代わりに宝石マークと旗マークの色からレアリティ・陣営を判定してください（後述）。

【宝石マーク（カード名の上）によるレアリティ判定】
カード番号が読み取れない場合、カード名の上にある宝石マークの色でレアリティを判定します。
・宝石マークなし（白い円）→ C / H / T のいずれか。識別文字列から読み取れない場合はC
・青色の宝石マーク → R または F 。識別文字列から読み取れない場合はR。
・銅色の宝石マーク → E（エグザルテッド）
・金色の宝石マーク → U（ユニーク）

【旗マーク（カード右上）による陣営判定】
カード右上にある旗マークの色で陣営を判定します。
・茶色の旗に歯車とねじの模様 → Axiom
・赤い旗に炎が出ている輪の模様 → Bravos
・ピンクの旗に竪琴の模様 → Lyra
・緑の旗に網目状に絡まった紐の模様 → Muna
・青い旗に斜めの正方形が重なり合った模様 → Ordis
・紫の旗に上を向いた目のような模様 → Yzmir

【カードテキストの記号変換ルール（必ず適用すること）】
以下のアイコン・記号を指定の文字列に置き換えてテキストを出力してください。

・テキスト欄に手のアイコンがある → [Recto] と書く
・テキスト欄にカードが光っているようなアイコン（予約アイコン）がある → [Turbo] と書く
・テキスト欄に右向き矢印のアイコンがある → [Dual] と書く
・テキスト欄に丸で囲まれた数字（①②③など）がある → [1][2][3] のように角括弧付きの数字に置き換える
・テキスト欄にカードの上にXが書いてあるアイコン（破棄アイコン）がある → [Discard] と書く
・サポート能力欄の識別方法と読み取り方:
  カード下部のテキスト欄は2つの領域に分かれています。サポート能力については持っていないカードも存在します。
  上側の領域（MAIN_EFFECT）: ユニークかエグザルテッドの場合、背景が透明でカードイラストが透けて見える。それ以外のレアリティでは、背景が白に近い色で塗りつぶされている。
  下側の領域（ECHO_EFFECT / サポート能力）: 背景がカード右上の旗マークと同じ陣営カラーで単一色に塗りつぶされている。
    （Axiom=茶色、Bravos=赤、Lyra=ピンク、Muna=緑、Ordis=青、Yzmir=紫、中立=グレー）
  この塗りつぶし背景の領域に書かれているテキストがサポート能力です。
  サポート能力が存在する場合は、そのテキストの先頭に [Support] を付けて card_text に続けてください。

【出力形式】
{
  "card_number": "ROC-102のように接頭辞-番号の形式（読み取れない場合はnull）",
  "rarity": "U など1文字（読み取れない場合は宝石マークから推定、それも不明ならnull）",
  "unique_number": "18245のような数字（ユニーク以外はnull）",
  "card_name": "カード上部もしくはカード中央（Permanentの場合）に書かれた英語のカード名",
  "faction": "Axiom / Bravos / Lyra / Muna / Ordis / Yzmir のいずれか（旗マークから判定、不明はnull）",
  "super_types": ["Token", "Expedition", "Landmark" の特殊タイプのリスト。なければ空リスト],
  "card_type": "Character / Permanent / Spell / Hero のいずれか",
  "card_subtypes": ["Mage", "Plant", "Feat" などサブタイプのリスト。なければ空リスト],
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


def _load_odt_examples(ref_path, max_samples=8):
    """翻訳スタイル参考例をODTまたはテキストファイルから取得する"""
    import os as _os
    if not ref_path or not _os.path.exists(ref_path):
        return ""
    try:
        if ref_path.lower().endswith(".odt"):
            import zipfile as _zf
            from lxml import etree as _etree
            with _zf.ZipFile(ref_path) as z:
                xml = z.read('content.xml')
            tree = _etree.fromstring(xml)
            ns = 'urn:oasis:names:tc:opendocument:xmlns:text:1.0'
            texts = []
            for p in tree.iter(f'{{{ns}}}p'):
                t = ''.join(p.itertext()).strip()
                if t and len(t) > 20:
                    texts.append(t)
            samples = texts[:max_samples]
        else:
            with open(ref_path, encoding="utf-8") as f:
                lines_all = [l.strip() for l in f if l.strip() and len(l.strip()) > 20]
            samples = lines_all[:max_samples]
        if not samples:
            return ""
        lines = ["【翻訳スタイル参考（過去の翻訳例。文体・表現を合わせること）】"]
        for s in samples:
            lines.append(f"・{s}")
        lines.append("")
        return "\n".join(lines)
    except Exception:
        return ""


def translate_card(api_key, card_name, card_text, csv_path=None, keywords_path=None,
                   super_types=None, card_type=None, card_subtypes=None,
                   odt_path=None):
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
            "・記号（[ウラ]/[表]/[両面]/＜サポート＞/[捨て札]/[永続]）: 括弧ごと固定表記を使う",
            "・カードタイプ（Character/Permanent/Spell/Hero）: 日本語のみ表記（英語名不要）。例: Character → キャラクター",
            "・カンマ区切りで二つ名を表しているカード名は、日本語のカード名では二つ名と名前の順を入れ替える。例: Leo, Relic Expert → 遺物の専門家、レオ",
            "・&区切りの物は二つ名ではないためそのままの順序で訳す。&は「と」と訳す。 例: Akesha & Taru → アケシャとタル",
            "・トークンを生成する個数が1個の場合は数を省略しない。例: create a ～ token -> ～トークン1個を生成する",
            "・生け贄に捧げる個数が1個の場合は数を省略しない。例: sacrefice a character -> キャラクター1体を生け贄に捧げる",
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

    # ODTファイルからスタイル参考例を取得
    odt_examples_text = _load_odt_examples(odt_path)

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
    if card_type == "Permanent" and super_types and keywords:
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
{odt_examples_text}
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
