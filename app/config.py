import os
from pathlib import Path
from dotenv import load_dotenv

# プロジェクトルートの .env を読み込む
_root = Path(__file__).parent.parent
load_dotenv(_root / ".env")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# CSVファイルのパス（Androidでは /sdcard/AlteredTcg/ 以下に置く想定）
CSV_PATH = os.environ.get("CSV_PATH", "AlteredTcgJp.csv")
UNIQUES_CSV_PATH = os.environ.get("UNIQUES_CSV_PATH", "uniques.csv")

# キーワード辞書CSVのパス
KEYWORDS_PATH = os.environ.get("KEYWORDS_PATH", "keywords.csv")

# 和訳シールテンプレートのパス
TEMPLATE_DOCX_PATH = os.environ.get("TEMPLATE_DOCX_PATH", "和訳シールテンプレ.docx")

# 出力docxの保存先ディレクトリ
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", ".")
