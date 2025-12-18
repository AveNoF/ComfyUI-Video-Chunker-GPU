# ComfyUI Video Chunker & Parallel Executor

[ [Japanese](#japanese) | [English](#english) ]

---

<a name="japanese"></a>
## 🇯🇵 日本語 (Japanese)

**ComfyUI Video Chunker** は、AnimateDiffやVid2Vidなどの長尺動画生成において発生する **「メインメモリ（RAM）不足によるクラッシュ（OOM）」** を回避し、さらに **並列処理** によって高速化するためのPythonスクリプトです。

### ✨ 特徴
1. **メモリリークの完全回避**: 動画を小さなチャンク（例: 100フレーム）に分割し、1チャンク処理するごとにサブプロセスを「破棄・再起動」することで、OSレベルでメモリを強制解放します。
2. **並列実行 (Parallel Workers)**: VRAMに余裕がある場合（RTX 3090/4090など）、複数のWorkerを同時に走らせて処理速度を倍増させることができます。
3. **自動結合**: 全パートの処理完了後、FFmpegを使って自動的に1本の動画に結合します。
4. **レジューム機能**: 処理が中断しても、生成済みのパートをスキップして続きから再開します。

### 📂 推奨ディレクトリ構成
このツールは **ComfyUIフォルダの中に入れる必要はありません**。ComfyUIフォルダの「横」に配置することを推奨します。

```text
/home/username/
  ├── ComfyUI/                  # 既存のComfyUI本体
  │    └── output/              # ※スクリプトはこの中に出力されたパーツを探しに行きます
  │
  └── ComfyUI-Video-Chunker-GPU/ # ★このツール
       ├── process_video.py
       ├── workflow_api.json
       └── ...