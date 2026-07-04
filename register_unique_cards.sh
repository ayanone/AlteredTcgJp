#!/bin/sh
# register_unique_cards.sh
# 使い方: sh register_unique_cards.sh "画像ファイルのパス"
#
# 画像内のユニークカードを Gemini API で認識し、uniques.csv に登録します。
# .venv 仮想環境を自動作成し、必要なパッケージをインストールします。

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

IMAGE="$1"
if [ -z "$IMAGE" ]; then
    echo "使い方: sh register_unique_cards.sh \"画像ファイルのパス\""
    exit 1
fi

# ── 仮想環境の作成 ──────────────────────────────────────
if [ ! -d ".venv" ]; then
    echo "仮想環境を作成しています (.venv) ..."
    python -m venv .venv
fi

if [ -f ".venv/Scripts/python" ]; then
    PYTHON=".venv/Scripts/python"   # Windows (Git Bash / MSYS2)
else
    PYTHON=".venv/bin/python"       # macOS / Linux
fi

# ── 依存パッケージのインストール ────────────────────────
echo "依存パッケージを確認しています..."
"$PYTHON" -m pip install --quiet --upgrade pip
"$PYTHON" -m pip install --quiet -r requirements.txt

# ── メインスクリプトを実行 ──────────────────────────────
echo ""
"$PYTHON" app/register_unique_cards.py "$IMAGE"
