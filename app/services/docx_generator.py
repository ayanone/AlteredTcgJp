"""
和訳シールdocxを生成する。
テンプレート(和訳シールテンプレ.docx)の構造を踏襲:
  - A4縦、3カラム、游ゴシック Light 6pt
  - 1カードのテキスト = 1段落（改行で区切り）
"""
import copy
import os
import zipfile
import shutil
import re
from datetime import datetime
from lxml import etree


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _make_run(text, font="游ゴシック Light", sz="12", underline=False):
    """1つの <w:r> 要素を作る"""
    r = etree.Element(f"{{{W}}}r")
    rpr = etree.SubElement(r, f"{{{W}}}rPr")
    fonts = etree.SubElement(rpr, f"{{{W}}}rFonts")
    fonts.set(f"{{{W}}}ascii", font)
    fonts.set(f"{{{W}}}hAnsi", font)
    fonts.set(f"{{{W}}}eastAsia", font)
    size = etree.SubElement(rpr, f"{{{W}}}sz")
    size.set(f"{{{W}}}val", sz)
    szcs = etree.SubElement(rpr, f"{{{W}}}szCs")
    szcs.set(f"{{{W}}}val", sz)
    if underline:
        u = etree.SubElement(rpr, f"{{{W}}}u")
        u.set(f"{{{W}}}val", "single")
    t = etree.SubElement(r, f"{{{W}}}t")
    if text.startswith(" ") or text.endswith(" "):
        t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    t.text = text
    return r


def _parse_runs(text, font="游ゴシック Light", sz="12"):
    """
    _テキスト_ 形式を下線付きrunに変換してrunのリストを返す。
    アンダースコアで囲まれた部分が下線付きになる。
    """
    import re
    runs = []
    for part in re.split(r"(_[^_]+_)", text):
        if part.startswith("_") and part.endswith("_") and len(part) > 2:
            runs.append(_make_run(part[1:-1], font=font, sz=sz, underline=True))
        elif part:
            runs.append(_make_run(part, font=font, sz=sz))
    return runs


def _make_br(font="游ゴシック Light", sz="12"):
    """改行 <w:r><w:br/></w:r>"""
    r = etree.Element(f"{{{W}}}r")
    rpr = etree.SubElement(r, f"{{{W}}}rPr")
    fonts = etree.SubElement(rpr, f"{{{W}}}rFonts")
    fonts.set(f"{{{W}}}ascii", font)
    fonts.set(f"{{{W}}}hAnsi", font)
    fonts.set(f"{{{W}}}eastAsia", font)
    size = etree.SubElement(rpr, f"{{{W}}}sz")
    size.set(f"{{{W}}}val", sz)
    szcs = etree.SubElement(rpr, f"{{{W}}}szCs")
    szcs.set(f"{{{W}}}val", sz)
    etree.SubElement(r, f"{{{W}}}br")
    return r


def _make_paragraph(card_number, rarity, name_jp, ability_jp):
    """1枚のカード分の <w:p> を作る"""
    p = etree.Element(f"{{{W}}}p")
    ppr = etree.SubElement(p, f"{{{W}}}pPr")
    style = etree.SubElement(ppr, f"{{{W}}}pStyle")
    style.set(f"{{{W}}}val", "Normal")

    # カード番号 + 日本語名（1行目）
    header = f"{card_number}-{rarity} {name_jp}"
    p.append(_make_run(header))
    p.append(_make_br())

    # 能力テキスト（改行ごとにbrで区切る、_テキスト_は下線付き）
    lines = ability_jp.splitlines()
    for i, line in enumerate(lines):
        if line.strip():
            for run in _parse_runs(line):
                p.append(run)
        if i < len(lines) - 1:
            p.append(_make_br())

    return p


def add_card_to_docx(docx_path, card_number, rarity, name_jp, ability_jp):
    """
    既存のdocxにカード1枚分のテキストを追記する。
    docxが存在しない場合はテンプレートからコピーして作成する。
    """
    from app.config import TEMPLATE_DOCX_PATH

    # テンプレートが存在しない場合はエラー
    if not os.path.exists(TEMPLATE_DOCX_PATH):
        raise FileNotFoundError(f"テンプレートが見つかりません: {TEMPLATE_DOCX_PATH}")

    # 出力ファイルがなければテンプレートからコピー
    if not os.path.exists(docx_path):
        shutil.copy2(TEMPLATE_DOCX_PATH, docx_path)
        # テンプレートのサンプルテキストを空にする
        _clear_body(docx_path)

    # docxを開いてdocument.xmlを編集
    _append_paragraph(docx_path, card_number, rarity, name_jp, ability_jp)


def _clear_body(docx_path):
    """docxのbodyを空にする（sectPrは残す）"""
    with zipfile.ZipFile(docx_path, "r") as z:
        xml = z.read("word/document.xml")
    tree = etree.fromstring(xml)
    body = tree.find(f"{{{W}}}body")
    sect_pr = body.find(f"{{{W}}}sectPr")
    for child in list(body):
        body.remove(child)
    if sect_pr is not None:
        body.append(sect_pr)
    _write_xml_to_docx(docx_path, "word/document.xml", etree.tostring(tree, xml_declaration=True, encoding="UTF-8", standalone=True))


def _append_paragraph(docx_path, card_number, rarity, name_jp, ability_jp):
    """docxのbodyにカード段落を追加する"""
    with zipfile.ZipFile(docx_path, "r") as z:
        xml = z.read("word/document.xml")
    tree = etree.fromstring(xml)
    body = tree.find(f"{{{W}}}body")
    sect_pr = body.find(f"{{{W}}}sectPr")

    new_p = _make_paragraph(card_number, rarity, name_jp, ability_jp)

    if sect_pr is not None:
        sect_pr.addprevious(new_p)
    else:
        body.append(new_p)

    _write_xml_to_docx(docx_path, "word/document.xml", etree.tostring(tree, xml_declaration=True, encoding="UTF-8", standalone=True))


def _write_xml_to_docx(docx_path, inner_path, xml_bytes):
    """zipファイル内の特定ファイルを書き換える"""
    tmp_path = docx_path + ".tmp"
    with zipfile.ZipFile(docx_path, "r") as zin:
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                if item.filename == inner_path:
                    zout.writestr(item, xml_bytes)
                else:
                    zout.writestr(item, zin.read(item.filename))
    os.replace(tmp_path, docx_path)


def get_output_path(output_dir):
    """日付ベースの出力ファイル名を返す"""
    today = datetime.now().strftime("%y%m%d")
    return os.path.join(output_dir, f"{today}和訳シール.docx")
