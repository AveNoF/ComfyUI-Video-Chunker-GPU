# ComfyUI Video Chunker & Parallel Executor + Sync Fixer

[ [Japanese](#japanese) | [English](#english) ]

---

<a name="japanese"></a>
## 🇯🇵 日本語 (Japanese)

**ComfyUI Video Chunker** は、AnimateDiffやVid2Vidなどの長尺動画生成において発生する **「メインメモリ（RAM）不足によるクラッシュ（OOM）」** を回避し、さらに **並列処理** によって高速化するための統合ツールセットです。

最新版では、AI動画特有の「音ズレ」や「再生速度（FPS）の不一致」を自動修正する強力なポストプロセス機能も搭載しました。

### ✨ 主な機能

#### 1. 生成・変換フェーズ (`process_video.py` / `batch_run.py`)
* **メモリリーク完全回避**: 動画を指定フレーム数（例: 1000）ごとに分割し、サブプロセスを「破棄・再起動」することでメモリを常にクリーンに保ちます。
* **並列実行 (Parallel Workers)**: RTX 3090/4090などの大容量VRAM向けに、複数の処理を同時実行して倍速化します。
* **音声自動合成**: 生成された映像と元の音声を結合し、FPS（再生速度）を強制的に元動画に合わせて音ズレを防ぎます。
* **簡単起動**: `run.sh` を使えば、仮想環境 (venv) の有効化を自動で行います。

#### 2. 修復・同期フェーズ (`batch_fix_sync.py`)
* **全自動修復工場**: フォルダに「元動画」と「生成されたAI動画（バラバラのままでOK）」を入れておけば、自動でペアを見つけて結合・FPS補正・高画質化を行います。
* **時間のゴム伸縮**: AI動画の尺が微妙に合わない場合でも、元動画の長さに合わせて映像をミリ秒単位で伸縮（リタイム）させ、完全に同期させます。

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
       ├── batch_run.py         # 動画生成バッチマネージャー
       ├── batch_fix_sync.py    # ★音ズレ修復・結合バッチ
       ├── process_video.py     # 変換コアロジック
       ├── workflow_api.json    # ComfyUIワークフロー
       ├── input_videos/        # ★ここに変換したい動画を入れる
       ├── queue_done/          # ★終わった元動画はここに移動される
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

2.  **【重要】ComfyUI側の準備 (別PCで行う場合など)**
    `workflow_api.json` はあくまで「レシピ」です。**料理道具（カスタムノード）はComfyUI側にインストールされている必要があります。**
    
    * **カスタムノード**: ComfyUI-Managerの **"Install Missing Custom Nodes"** を使い、JSON内で使われているノード（Impact Pack, VideoHelperSuiteなど）を全てインストールしてください。
    * **モデル**: CheckpointやVAEモデルも、ComfyUIの `models` フォルダにコピーしておく必要があります。
    * **依存ライブラリ**: ComfyUIのvenv環境にも `piexif` が必要です。
        ```bash
        cd ~/ComfyUI
        source venv/bin/activate
        pip install piexif
        ```

3.  **ワークフローの配置**
    ComfyUIで動画変換用ワークフローを作り、メニューの **"Save (API format)"** でJSONを保存してください。
    これを `workflow_api.json` という名前でスクリプトと同じフォルダに置きます。
    * **必須:** 動画読み込みノード (`VHS_LoadVideo`)
    * **必須:** 動画保存ノード (`VHS_VideoCombine`)

#### 実行
1.  変換したい動画ファイル（mp4, avi, mov, mkv）を **`input_videos`** フォルダに入れます。
2.  以下のコマンドで実行します。

    ```bash
    # 簡単起動（venv自動検知）
    ./run.sh
    ```

処理が完了すると、ComfyUIの `output` フォルダに結合済み動画（音声付き）が保存されます。

---

### 🔧 使い方 2: 音ズレ・FPS修復 (The Fixer)

生成された動画の音がズレている、またはFPSがおかしい場合にのみ使用します。

1.  以下のコマンドを実行し、作業用フォルダを作成させます。
    ```bash
    python batch_fix_sync.py
    ```
2.  作成された `fix_work` フォルダ内にファイルを配置します。
    * **`fix_work/Origin`**: 音声が正しい「元の動画」を入れる。
    * **`fix_work/AInized`**: ComfyUIが出力した大量の分割ファイル (`xxx_part_001.mp4`...) をフォルダごと、あるいは中身を全て入れます。
3.  もう一度実行します。
    ```bash
    python batch_fix_sync.py
    ```
4.  スクリプトが自動的にペアを見つけ、時間を伸縮させて同期し、`Fixed_Output` に保存します。

---

### ⚙️ 設定の変更

`process_video.py` 内の定数を書き換えることでパフォーマンス調整が可能です。

```python
CHUNK_SIZE = 1000          # 1回に処理するフレーム数（推奨: 500~1000）
MAX_PARALLEL_WORKERS = 1   # 同時実行数 (RTX 3060なら1, RTX 3090/4090なら2も可)
```

---

<a name="english"></a>
## 🇺🇸 English

**ComfyUI Video Chunker** is a toolset designed to prevent **System RAM Out-Of-Memory (OOM)** crashes and accelerate processing via **Parallel Execution** when generating long videos (e.g., AnimateDiff, Vid2Vid) in ComfyUI.

It also includes a powerful post-processing tool to automatically fix "Audio Sync" and "FPS Mismatch" issues common in AI video generation.

### ✨ Features

#### 1. Generation Phase
* **Prevent Memory Leaks**: Splits video into chunks. Spawns/kills subprocesses for each chunk to force OS-level memory release.
* **Parallel Execution**: Run multiple workers simultaneously (Great for RTX 3090/4090).
* **Audio Muxing**: Automatically merges original audio and corrects FPS during the final merge.
* **Easy Launcher**: `run.sh` auto-detects and activates venv.

#### 2. Fix & Sync Phase (`batch_fix_sync.py`)
* **Auto-Fix Factory**: Just put "Original Videos" and "AI Chunk Files" into folders. The script automatically pairs them, merges chunks, and fixes sync issues.
* **Time Stretching**: If the AI video length doesn't match the original, it uses `setpts` to stretch/shrink the video to match the audio perfectly.

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

2.  **[Important] ComfyUI Setup (For New Machines)**
    The `workflow_api.json` is just a recipe. **You must install the actual custom nodes on ComfyUI.**
    
    * **Custom Nodes**: Use ComfyUI-Manager's **"Install Missing Custom Nodes"** feature to install all nodes used in your JSON (e.g., Impact Pack, VideoHelperSuite).
    * **Models**: Ensure Checkpoints and VAEs are copied to the ComfyUI `models` folder.
    * **Dependencies**: You must install `piexif` in your ComfyUI environment.
        ```bash
        cd ~/ComfyUI
        source venv/bin/activate
        pip install piexif
        ```

3.  **Workflow**
    Save your ComfyUI workflow as **API format** JSON. Name it `workflow_api.json` and place it in the script folder.

#### Execution
1.  Place video files into **`input_videos`**.
2.  Run:
    ```bash
    ./run.sh
    ```

---

### 🔧 Usage 2: Fixing Sync/FPS Issues

Use this if your generated video has audio desync.

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
4.  The script will identify pairs, retiming the video to match the audio, and save to `Fixed_Output`.

---

## Requirements
* Python 3.10+
* FFmpeg (must be in system PATH)
* ComfyUI (running on port 8188)
* NVIDIA GPU (RTX 3060/3090 tested)

## License
MIT