#!/bin/bash

# このスクリプトがある場所に移動（どこから実行してもOKにするため）
cd "$(dirname "$0")"

echo "=== ComfyUI Video Chunker Launcher ==="

# 1. カレントディレクトリに venv があれば使う
if [ -d "venv" ]; then
    echo "Using local venv..."
    source venv/bin/activate

# 2. なければ、隣の ComfyUI/venv を探しに行く (ComfyUI環境を流用)
elif [ -d "../ComfyUI/venv" ]; then
    echo "Using ComfyUI venv..."
    source ../ComfyUI/venv/bin/activate
else
    echo "Warning: No venv found. Running with system python..."
fi

# 実行
python batch_run.py

# 終了確認
echo ""
echo "処理が完了しました。エンターキーを押すと閉じます。"
read