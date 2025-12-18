# ComfyUI Video Chunker & Parallel Executor

[ [Japanese](#japanese) | [English](#english) ]

---

<a name="japanese"></a>
## 🇯🇵 日本語 (Japanese)

**ComfyUI Video Chunker** は、AnimateDiffやVid2Vidなどの長尺動画生成において発生する **「メインメモリ（RAM）不足によるクラッシュ（OOM）」** を回避し、さらに **並列処理** によって高速化するためのツールセットです。

### ✨ 主な機能
1. **メモリリーク完全回避**: 動画を分割（チャンク化）し、1区画ごとにサブプロセスを「破棄・再起動」することで、メモリを常にクリーンな状態に保ちます。
2. **バッチ処理 & 自動整理**: `input_videos` フォルダに動画を入れるだけで次々と処理し、終わったら `queue_done` へ移動します。
3. **並列実行 (Parallel Workers)**: RTX 3090/4090などの強いGPU向けに、複数の処理を同時実行して倍速化します。
4. **自動リネーム**: 処理完了後、生成されたファイルを `元のファイル名_upscaled.mp4` にリネームして保存します。
5. **簡単起動**: `run.sh` を使えば、仮想環境 (venv) の有効化を自動で行います。

### 📂 推奨ディレクトリ構成
このツールは **ComfyUIフォルダの「横」** に配置することを推奨します。

```text
/home/username/
  ├── ComfyUI/                  # 既存のComfyUI本体
  │    ├── venv/                # (あれば) ここの仮想環境を自動で借ります
  │    └── output/              # ※スクリプトはこの中に出力されたパーツを探しに行きます
  │
  └── ComfyUI-Video-Chunker-GPU/ # ★このツール
       ├── run.sh               # 起動スクリプト
       ├── batch_run.py         # バッチ処理マネージャー
       ├── process_video.py     # 変換コアロジック
       ├── workflow_api.json    # ComfyUIワークフロー
       ├── input_videos/        # ★ここに変換したい動画を入れる
       └── queue_done/          # ★終わった動画はここに移動される

       🚀 使い方
1. 準備
リポジトリをクローンし、ライブラリを入れます。

Bash

git clone [https://github.com/AveNoF/ComfyUI-Video-Chunker-GPU.git](https://github.com/AveNoF/ComfyUI-Video-Chunker-GPU.git)
cd ComfyUI-Video-Chunker-GPU
pip install -r requirements.txt
# または ComfyUIのvenvを使ってもOK
2. ワークフローの用意
ComfyUIで動画変換用ワークフローを作り、メニューの "Save (API format)" でJSONを保存してください。 これを workflow_api.json という名前でスクリプトと同じフォルダに置きます。

必須: 動画読み込みノード (VHS_LoadVideo)

必須: 動画保存ノード (VHS_VideoCombine)

3. 実行
変換したい動画ファイル（mp4, avi, mov, mkv）を input_videos フォルダに入れます（複数可）。

その後、以下のコマンドで実行します。

Bash

# 簡単起動（venv自動検知）
./run.sh

# または手動実行
python batch_run.py
処理が始まると、順次変換が行われ、完了した元動画は queue_done に移動します。 完成した動画は ComfyUI の output フォルダに xxxxx_upscaled.mp4 として保存されます。

⚙️ 設定の変更
process_video.py 内の定数を書き換えることでパフォーマンス調整が可能です。

Python

CHUNK_SIZE = 100           # 1回に処理するフレーム数（メモリ不足なら減らす）
MAX_PARALLEL_WORKERS = 2   # 同時実行数（VRAM容量に合わせて 1 または 2 に設定）
<a name="english"></a>