# ComfyUI Video Chunker & Parallel Executor + Sync Fixer

[ [Japanese](#japanese) | [English](#english) ]

---

<a name="japanese"></a>
## 🇯🇵 日本語 (Japanese)

**ComfyUI Video Chunker** は、AnimateDiffやVid2Vidなどの長尺動画生成において発生する **「メインメモリ（RAM）不足によるクラッシュ（OOM）」** を回避するための統合ツールセットです。

最新版（v2.0）では、**「音ズレ防止」** に特化しました。AIによるフレーム補間（ヌルヌル化）を行わず、**「元の動画のFPS（速度）とコマ数を完全に維持したまま画質だけを上げる」** ことで、音声との完全な同期を実現します。

### ✨ 主な機能

#### 1. 生成・変換フェーズ (`process_video.py`)
* **メモリリーク完全回避**: 動画を指定フレーム数（例: 1000）ごとに分割し、サブプロセスを「破棄・再起動」することでメモリを常にクリーンに保ちます。
* **FPS完全維持**: 元動画のフレームレート（例: 23.976fps）を読み取り、ComfyUIの出力設定を自動的に書き換えて強制同期させます。
* **音声自動合成**: 生成された映像（再エンコードなし）と元の音声をロスレスで結合します。
* **簡単起動**: `run.sh` を使えば、仮想環境 (venv) の有効化を自動で行います。

#### 2. 修復・結合ツール (`batch_fix_sync.py`)
* **全自動結合工場**: フォルダに「元動画」と「生成されたAI動画（バラバラのままでOK）」を入れておけば、自動でペアを見つけて結合・音声合成を行います。
* **無劣化合成**: 映像データは再エンコードせず (`copy`)、元の音声を載せるため、画質劣化がなく処理も一瞬で終わります。

### 📂 推奨ディレクトリ構成
このツールは **ComfyUIフォルダの「横」** に配置することを推奨します。

```text
/home/username/
  ├── ComfyUI/                  # 既存のComfyUI本体
  │    ├── venv/                # (あれば) ここの仮想環境を自動で借ります
  │    └── output/              # ※スクリプトはこの中に出力されたパーツを探しに行きます
  │
  └── ComfyUI-Video-Chunker-GPU/ # ★このツール
       ├── run.sh               # 生成ランチャー（ダブルクリックで実行）
       ├── batch_fix_sync.py    # ★手動結合・修復ツール
       ├── process_video.py     # 変換コアロジック
       ├── workflow_api.json    # ComfyUIワークフロー
       ├── input_videos/        # ★ここに変換したい動画を入れる
       └── fix_work/            # ★修復作業用（batch_fix_sync.pyを実行すると生成）
             ├── Origin/        # (修復用) 元動画を入れる
             ├── AInized/       # (修復用) 生成されたAI動画を入れる
             └── Fixed_Output/  # (修復用) 完成品が出る
```

### 🚀 使い方 1: 動画生成 (Upscale / Vid2Vid)

#### 準備
1.  リポジトリをクローンし、ライブラリを入れます。
    ```bash
    git clone [https://github.com/AveNoF/ComfyUI-Video-Chunker-GPU.git](https://github.com/AveNoF/ComfyUI-Video-Chunker-GPU.git)
    cd ComfyUI-Video-Chunker-GPU
    
    # 仮想環境作成 (Ubuntu 24.04+ 推奨)
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
    ```

2.  **【重要】ComfyUI側の準備**
    `workflow_api.json` はあくまで「レシピ」です。**料理道具（カスタムノード）はComfyUI側にインストールされている必要があります。**
    
    * **必須:** ComfyUI-Manager等で、JSON内で使われているノード（VideoHelperSuiteなど）をインストールしてください。
    * **必須:** **RIFEなどのフレーム補間ノードは使用しないでください。** 音ズレの原因になります。
    * **依存ライブラリ:** ComfyUIのvenv環境にも `piexif` が必要です。
        ```bash
        cd ~/ComfyUI
        source venv/bin/activate
        pip install piexif
        ```

3.  **ワークフローの配置**
    ComfyUIで動画変換用ワークフローを作り、メニューの **"Save (API format)"** でJSONを保存してください。
    これを `workflow_api.json` という名前でスクリプトと同じフォルダに置きます。

#### 実行
1.  変換したい動画ファイル（mp4, avi, mov, mkv）を **`input_videos`** フォルダに入れます。
2.  以下のコマンドで実行します。

    ```bash
    # 簡単起動（venv自動検知）
    ./run.sh
    ```

処理が完了すると、ComfyUIの `output` フォルダに結合済み動画（音声付き）が保存されます。

---

### 🔧 使い方 2: 手動結合・修復 (The Fixer)

生成された動画パーツを手動で結合したい場合や、別のPCで生成したものをまとめたい場合に使用します。

1.  以下のコマンドを実行し、作業用フォルダを作成させます。
    ```bash
    python batch_fix_sync.py
    ```
2.  作成された `fix_work` フォルダ内にファイルを配置します。
    * **`fix_work/Origin`**: 音声が正しい「元の動画」を入れる。
    * **`fix_work/AInized`**: ComfyUIが出力した大量の分割ファイル (`xxx_part_001.mp4`...) を全て入れます。
3.  もう一度実行します。
    ```bash
    python batch_fix_sync.py
    ```
4.  スクリプトが自動的にペアを見つけ、劣化なしで結合・音声合成を行い `Fixed_Output` に保存します。

---

### ⚙️ 設定の変更

`process_video.py` 内の定数を書き換えることでパフォーマンス調整が可能です。

```python
CHUNK_SIZE = 1000          # 1回に処理するフレーム数（推奨: 500~1000）
MAX_PARALLEL_WORKERS = 1   # 並列数 (RTX 3090/4090なら2も可だが、1が最も安定)
```

---

<a name="english"></a>
## 🇺🇸 English

**ComfyUI Video Chunker** is a toolset designed to prevent **System RAM Out-Of-Memory (OOM)** crashes when generating long videos (e.g., AnimateDiff, Vid2Vid) in ComfyUI.

Version 2.0 focuses on **Exact FPS Preservation** to prevent audio sync issues. It avoids frame interpolation and strictly maintains the source video's frame rate.

### ✨ Features

#### 1. Generation Phase (`process_video.py`)
* **Prevent Memory Leaks**: Splits video into chunks to force OS-level memory release.
* **Exact FPS**: Reads the source video FPS (e.g., 23.976) and forces ComfyUI to output at the exact same rate.
* **Audio Muxing**: Automatically merges original audio without re-encoding the video stream.
* **Easy Launcher**: `run.sh` auto-detects and activates venv.

#### 2. Batch Fixer (`batch_fix_sync.py`)
* **Auto-Merge Factory**: Simply place "Original Videos" and "AI Chunk Files" into folders. The script automatically pairs them and merges them.
* **Lossless Muxing**: Uses `ffmpeg -c:v copy` to merge video chunks instantly without quality loss.

### 🚀 Usage 1: Generating Videos

#### Preparation
1.  Clone and install.
    ```bash
    git clone [https://github.com/AveNoF/ComfyUI-Video-Chunker-GPU.git](https://github.com/AveNoF/ComfyUI-Video-Chunker-GPU.git)
    cd ComfyUI-Video-Chunker-GPU
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
    ```

2.  **[Important] ComfyUI Requirements**
    * **Custom Nodes**: Install missing nodes (like VideoHelperSuite) via ComfyUI-Manager.
    * **No Interpolation**: Do **NOT** use RIFE or frame interpolation nodes.
    * **Dependencies**: You must install `piexif` in your ComfyUI environment.
        ```bash
        cd ~/ComfyUI
        source venv/bin/activate
        pip install piexif
        ```

3.  **Workflow**
    Save your ComfyUI workflow as **API format** JSON named `workflow_api.json` and place it in the script folder.

#### Execution
1.  Place video files into **`input_videos`**.
2.  Run:
    ```bash
    ./run.sh
    ```

---

### 🔧 Usage 2: Manual Merging

Use this if you have raw chunk files and want to merge them later.

1.  Run the script to generate folders:
    ```bash
    python batch_fix_sync.py
    ```
2.  Place files into the created `fix_work` directory:
    * **`fix_work/Origin`**: Place original videos here.
    * **`fix_work/AInized`**: Place all AI output chunks (`xxx_part_001.mp4`...) here.
3.  Run again:
    ```bash
    python batch_fix_sync.py
    ```

---

## Requirements
* Python 3.10+
* FFmpeg (must be in system PATH)
* ComfyUI (running on port 8188)
* NVIDIA GPU (RTX 3060/3090 tested)

## License
MIT