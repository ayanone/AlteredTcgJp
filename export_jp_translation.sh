#!/bin/sh
# export_jp_translation.sh
# 使い方: sh export_jp_translation.sh "画像ファイルのパス" [出力先ディレクトリ]
#
# 画像内のカードを Gemini API で認識し、和訳シール PDF を出力します。
# .venv 仮想環境を自動作成し、必要なパッケージをインストールします。

set -e

# スクリプトのあるディレクトリに移動
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# 引数チェック
IMAGE="$1"
if [ -z "$IMAGE" ]; then
    echo "使い方: sh export_jp_translation.sh \"画像ファイルのパス\" [出力先ディレクトリ]"
    exit 1
fi

OUTPUT_DIR="${2:-.}"

# ── 仮想環境の作成 ──────────────────────────────────────
if [ ! -d ".venv" ]; then
    echo "仮想環境を作成しています (.venv) ..."
    python -m venv .venv
fi

# OS によって activate のパスが異なる
if [ -f ".venv/Scripts/python" ]; then
    PYTHON=".venv/Scripts/python"   # Windows (Git Bash / MSYS2)
else
    PYTHON=".venv/bin/python"       # macOS / Linux
fi

# ── 依存パッケージのインストール ────────────────────────
echo "依存パッケージを確認しています..."
"$PYTHON" -m pip install --quiet --upgrade pip
"$PYTHON" -m pip install --quiet python-dotenv lxml docx2pdf reportlab

# ── メインスクリプトを実行 ──────────────────────────────
echo ""
"$PYTHON" export_jp_translation.py "$IMAGE" "$OUTPUT_DIR"
