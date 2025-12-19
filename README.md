# ComfyUI Video Chunker & Parallel Executor + Sync Fixer (v3.0)

[ Japanese | English ]

<a name="japanese"></a> ## 🇯🇵 日本語 (Japanese)

ComfyUI Video Chunker は、AnimateDiffやVid2Vidなどの長尺動画生成において発生する 「メインメモリ（RAM）不足によるクラッシュ（OOM）」 を回避するための統合ツールセットです。

最新版（v3.0）では、「事前CFR変換（Pre-CFR Conversion）」 プロセスを導入しました。 生成を開始する前に、動画のフレームレートを強制的に固定（30fps等）し、フレームのばらつきを整地することで、音ズレや早送り現象を物理的に発生させない 仕組みになっています。

### ✨ 主な機能

#### 1. バッチ処理ランチャー (batch_run.py) * 自動CFR変換: 入力された動画（VFR/可変フレームレート）を、自動的にffmpegで固定フレームレート（CFR）に変換します。 * 確認フロー: 変換が終わった段階で一時停止し、ユーザーに生成を開始するか（Y/N）を確認します。 * 全自動生成: OKを選択すると、変換済みのクリーンな動画を使ってAI生成・結合・お片付けまでを一気に行います。

#### 2. 生成・変換フェーズ (process_video.py) * メモリリーク回避: 動画を指定フレーム数ごとに分割・再起動しながら処理します。 * 単純結合: 入力動画がすでに整っているため、複雑な計算なしで無劣化・完璧な同期を実現します。

#### 3. 修復・結合ツール (batch_fix_sync.py) * レスキューツール: 過去に生成して音がズレてしまった動画を、強制リタイミング計算で無理やり同期させて修復します（新規生成には使いません）。

### 📂 推奨ディレクトリ構成

```text /home/username/ ├── ComfyUI/ # 既存のComfyUI本体 │ ├── venv/ # (あれば) ここの仮想環境を自動で借ります │ └── output/ # ※スクリプトはこの中に出力されたパーツを探しに行きます │ └── ComfyUI-Video-Chunker-GPU/ # ★このツール ├── run.sh # ★起動コマンド ├── batch_run.py # 全自動マネージャー（CFR変換+生成） ├── process_video.py # 生成コアロジック ├── batch_fix_sync.py # (旧)修復ツール ├── workflow_api.json # ComfyUIワークフロー ├── input_videos/ # ★ここに変換したい動画を入れる │ └── temp_cfr_ready/ # (自動生成) 変換済み動画の一時置き場 └── queue_done/ # (自動生成) 終わった動画が移動される場所 ```

### 🚀 使い方 1: 動画生成 (Upscale / Vid2Vid)

#### 準備

リポジトリをクローンし、ライブラリを入れます。 ```bash git clone https://github.com/AveNoF/ComfyUI-Video-Chunker-GPU.git cd ComfyUI-Video-Chunker-GPU

仮想環境作成
python3 -m venv venv source venv/bin/activate pip install -r requirements.txt ```

【重要】ComfyUI側の準備 workflow_api.json はあくまで「レシピ」です。料理道具（カスタムノード）はComfyUI側にインストールされている必要があります。

* 必須: ComfyUI-Manager等で、JSON内で使われているノード（VideoHelperSuiteなど）をインストールしてください。 * 依存ライブラリ: ComfyUIのvenv環境にも piexif が必要です。 ```bash cd ~/ComfyUI source venv/bin/activate pip install piexif ```

ワークフローの配置 ComfyUIで動画変換用ワークフローを作り、メニューの "Save (API format)" でJSONを保存してください。 これを workflow_api.json という名前でスクリプトと同じフォルダに置きます。

#### 実行手順

変換したい動画ファイル（mp4, avi, mov, mkv）を input_videos フォルダに入れます。

以下のコマンドを実行します。

```bash ./run.sh ```

Phase 1: 変換 スクリプトが動画を検知し、自動的に 30fps (設定可) の固定フレームレート動画に変換します。

Phase 2: 確認 すべての変換が終わると、以下のように聞かれます。 ```text 🚀 Proceed with AI Upscaling for all files? (y/n): ``` * ここで y を入力すると、AI生成が始まります。 * 時間がない場合は n で中断できます（変換済みファイルは保持されます）。

Phase 3: 生成 ComfyUIによる生成、結合が行われます。完成品は ComfyUI/output に保存され、元の動画は queue_done に移動します。

### 🔧 使い方 2: 過去の動画の修復 (Fixer)

「このツール(v3.0)を使う前に生成して、音がズレてしまった動画」を直す場合に使用します。

python batch_fix_sync.py を実行してフォルダを作成します。

フォルダにファイルを入れます。 * fix_work/Origin: 音声が正しい「元動画」 * fix_work/AInized: 生成されたバラバラのパーツ (_part_001.mp4...)

もう一度 python batch_fix_sync.py を実行します。 * タイムスタンプを無視して強制的にリタイミングし、結合します。

### ⚙️ 設定の変更

batch_run.py 内でフレームレートなどを変更できます。

```python TARGET_FPS = 30 # 変換するフレームレート (30 or 60推奨) WORKFLOW_FILE = "workflow_api.json" ```

<a name="english"></a> ## 🇺🇸 English

ComfyUI Video Chunker is a toolset designed to prevent System RAM Out-Of-Memory (OOM) crashes when generating long videos (e.g., AnimateDiff, Vid2Vid) in ComfyUI.

Version 3.0 introduces a Pre-CFR Conversion Workflow. Before generation begins, input videos are automatically converted to a Constant Frame Rate (CFR). This ensures that the AI generates frames with perfect timing, eliminating audio desync and speed issues.

### ✨ Features

#### 1. Batch Manager (batch_run.py) * Auto CFR Conversion: Automatically converts VFR inputs to steady CFR (e.g., 30fps) videos using FFmpeg. * Confirmation Step: Pauses after conversion to ask if you want to proceed with the heavy AI generation phase (Y/N). * Automated Pipeline: Handles conversion, generation, merging, and cleanup in one go.

#### 2. Generator (process_video.py) * OOM Prevention: Splits video into chunks and restarts subprocesses to free RAM. * Perfect Sync: Since the input is pre-corrected, the output merges perfectly with the audio without complex calculations.

#### 3. Fixer (batch_fix_sync.py) * Rescue Tool: A standalone tool to fix previously generated videos that have desync issues using forced re-timing logic.

### 🚀 Usage 1: Generating Videos

#### Preparation

Clone and install. ```bash git clone https://github.com/AveNoF/ComfyUI-Video-Chunker-GPU.git cd ComfyUI-Video-Chunker-GPU

python3 -m venv venv source venv/bin/activate pip install -r requirements.txt ```

[Important] ComfyUI Requirements * Custom Nodes: Install nodes (like VideoHelperSuite) via ComfyUI-Manager. * Dependencies: You must install piexif in your ComfyUI environment. ```bash cd ~/ComfyUI source venv/bin/activate pip install piexif ```

Workflow Save your ComfyUI workflow as API format JSON named workflow_api.json and place it in the script folder.

#### Execution Steps

Place video files into input_videos.

Run: ```bash ./run.sh ```

Phase 1: Conversion The script converts all videos to CFR format.

Phase 2: Confirmation Wait for the prompt: ```text 🚀 Proceed with AI Upscaling for all files? (y/n): ``` Type y to start the AI generation.

Phase 3: Generation The script generates, merges, and moves the finished files to ComfyUI/output.

### 🔧 Usage 2: Fixing Old Videos

Use this only for videos generated before v3.0 that have sync issues.

Run python batch_fix_sync.py to create folders.

Place files: * fix_work/Origin: Original videos. * fix_work/AInized: AI chunk files.

Run python batch_fix_sync.py again.

## Requirements * Python 3.10+ * FFmpeg (must be in system PATH) * ComfyUI (running on port 8188) * NVIDIA GPU

## License MIT