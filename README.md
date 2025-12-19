<a name="japanese"></a> ## 🇯🇵 日本語 (Japanese)

ComfyUI Video Chunker は、AnimateDiffやVid2Vidなどの長尺動画生成において発生する 「メインメモリ（RAM）不足によるクラッシュ（OOM）」 を回避するための統合ツールセットです。

最新版（v3.0）では、「音ズレ防止」と「重複排除」 に特化した強力な同期エンジンを搭載しました。 可変フレームレート（VFR）の動画であっても、強制的に元の動画と同じ再生時間に補正（Time-Stretch） し、元の音声を合成することで、プロ品質の結合を実現します。

### ✨ 主な機能

#### 1. 生成・変換フェーズ (process_video.py)

メモリリーク完全回避: 動画を指定フレーム数（例: 1000）ごとに分割し、サブプロセスを「破棄・再起動」することでメモリを常にクリーンに保ちます。

絶対時間同期 (Absolute Duration Sync): AI生成時にフレーム数が多少変動しても、最終的に「元動画の秒数」にピタリと合うように映像を伸縮（Stretch）させます。

VFR完全対応: 可変フレームレートの動画でも音ズレしません。

重複ファイル自動排除: フォルダ内に同じパートのファイルが複数あっても、自動で最新の1つだけを選び、シーンの繰り返し事故を防ぎます。

高音質合成: 断片動画の音声は使わず、最後に元動画の音声を無劣化で載せ替えるため、つなぎ目のノイズがありません。

#### 2. 修復・結合ツール (batch_fix_sync.py)

全自動結合工場: フォルダに「元動画」と「バラバラのAI動画」を入れておけば、自動でペアを見つけて結合・修復します。

リカバリー機能: 過去に生成して「音がズレた」「シーンがループした」動画も、このツールを通すだけで完璧に直ります。

### 📂 推奨ディレクトリ構成 このツールは ComfyUIフォルダの「横」 に配置することを推奨します。

/home/username/
               ├── ComfyUI/ # 既存のComfyUI本体
               |                  │ 
               |                  ├── venv/ # (あれば) ここの仮想環境を自動で借ります 
               |                  │
               |                  └── output/ # ※スクリプトはこの中に出力されたパーツを探しに行きます 
               │ 
               └── ComfyUI-Video-Chunker-GPU/ # ★このツール
                                ├── run.sh # 生成ランチャー（ダブルクリックで実行） 
                                ├── batch_fix_sync.py # ★手動結合・修復ツール
                                ├── process_video.py # 変換コアロジック
                                ├── workflow_api.json # ComfyUIワークフロー
                                ├── input_videos/ # ★ここに変換したい動画を入れる 
                                └── fix_work/ # ★修復作業用（batch_fix_sync.pyを実行すると生成）
                                         ├── Origin/ # (修復用) 元動画を入れる
                                         ├── AInized/ # (修復用) 生成されたAI動画を入れる 
                                         └── Fixed_Output/ # (修復用) 完成品が出る

### 🚀 使い方 1: 動画生成 (Upscale / Vid2Vid)

#### 準備

リポジトリをクローンし、ライブラリを入れます。 git clone https://github.com/AveNoF/ComfyUI-Video-Chunker-GPU.git cd ComfyUI-Video-Chunker-GPU

# 仮想環境作成 (Ubuntu 24.04+ 推奨) python3 -m venv venv source venv/bin/activate pip install -r requirements.txt

【重要】ComfyUI側の準備 workflow_api.json はあくまで「レシピ」です。料理道具（カスタムノード）はComfyUI側にインストールされている必要があります。

必須: ComfyUI-Manager等で、JSON内で使われているノード（VideoHelperSuiteなど）をインストールしてください。

推奨: フレーム補間（RIFE等）は使用せず、単純なUpscale/Vid2Vid構成にしてください。

依存ライブラリ: ComfyUIのvenv環境にも piexif が必要です。 cd ~/ComfyUI source venv/bin/activate pip install piexif

ワークフローの配置 ComfyUIで動画変換用ワークフローを作り、メニューの "Save (API format)" でJSONを保存してください。 これを workflow_api.json という名前でスクリプトと同じフォルダに置きます。

注意: Video CombineノードのFPS設定は何でも構いません（スクリプトが自動調整します）。

#### 実行

変換したい動画ファイル（mp4, avi, mov, mkv）を input_videos フォルダに入れます。

以下のコマンドで実行します。

# 簡単起動（venv自動検知） ./run.sh

処理が完了すると、ComfyUIの output フォルダに結合済み動画（音声付き・絶対同期済み）が保存されます。

### 🔧 使い方 2: 手動結合・修復 (The Fixer)

「過去に作った動画の音がズレている」「シーンがループしている」場合や、別のPCで生成したパーツをまとめたい場合に使用します。

以下のコマンドを実行し、作業用フォルダを作成させます。 python batch_fix_sync.py

作成された fix_work フォルダ内にファイルを配置します。

fix_work/Origin: 音声が正しい「元動画」を入れる。

fix_work/AInized: ComfyUIが出力した大量の分割ファイル (xxx_part_001.mp4...) を全て入れます。

もう一度実行します。 python batch_fix_sync.py

スクリプトが自動的にペアを見つけ、重複を除去し、長さを元動画に合わせて結合します。完成品は Fixed_Output に保存されます。

### ⚙️ 設定の変更

process_video.py 内の定数を書き換えることで調整が可能です。

CHUNK_SIZE = 1000 # 1回に処理するフレーム数（推奨: 500~1000） MAX_PARALLEL_WORKERS = 1 # 基本は1（GPUメモリ不足を防ぐため） NODE_ID_SAVER = "4" # workflow_api.json内のVideo CombineノードID

<a name="english"></a> ## 🇺🇸 English

ComfyUI Video Chunker is a toolset designed to prevent System RAM Out-Of-Memory (OOM) crashes when generating long videos (e.g., AnimateDiff, Vid2Vid) in ComfyUI.

Version 3.0 introduces a Robust Sync Engine. It handles Variable Frame Rate (VFR) videos by forcing the AI video duration to match the original video exactly (Time-Stretch), ensuring perfect audio synchronization.

### ✨ Features

#### 1. Generation Phase (process_video.py)

Prevent Memory Leaks: Splits video into chunks and restarts subprocesses to free RAM.

Absolute Duration Sync: Stretches or compresses the video stream to match the original video's duration down to the millisecond.

VFR Support: Perfect sync even for variable frame rate sources.

Smart De-Duplication: Automatically detects and removes duplicate chunk files (e.g., if you ran generation twice), preventing scene loops.

High Quality Audio: Replaces chunk audio with the original master audio track at the final merge.

#### 2. Batch Fixer (batch_fix_sync.py)

Auto-Merge Factory: Simply place "Original Videos" and "AI Chunk Files" into folders. The script automatically pairs, filters duplicates, and merges them.

Repair Tool: Can fix previously generated videos that have audio desync or looping scenes.

### 🚀 Usage 1: Generating Videos

#### Preparation

Clone and install. git clone https://github.com/AveNoF/ComfyUI-Video-Chunker-GPU.git cd ComfyUI-Video-Chunker-GPU

python3 -m venv venv source venv/bin/activate pip install -r requirements.txt

[Important] ComfyUI Requirements

Custom Nodes: Install nodes (like VideoHelperSuite) via ComfyUI-Manager.

Dependencies: You must install piexif in your ComfyUI environment. cd ~/ComfyUI source venv/bin/activate pip install piexif

Workflow Save your ComfyUI workflow as API format JSON named workflow_api.json and place it in the script folder.

#### Execution

Place video files into input_videos.

Run: ./run.sh

### 🔧 Usage 2: Manual Merging / Fixing

Use this if you have raw chunk files and want to merge them later, or need to fix desync issues.

Run the script to generate folders: python batch_fix_sync.py

Place files into the created fix_work directory:

fix_work/Origin: Place original videos here.

fix_work/AInized: Place all AI output chunks (xxx_part_001.mp4...) here.

Run again: python batch_fix_sync.py

## Requirements

Python 3.10+

FFmpeg (must be in system PATH)

ComfyUI (running on port 8188)

NVIDIA GPU (RTX 3060/3090/4090 tested)

## License MIT
