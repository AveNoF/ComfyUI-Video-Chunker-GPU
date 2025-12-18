import os
import glob
import subprocess
import argparse
import datetime
import shutil

# ================= 設定エリア =================
# 作業用フォルダのルート名前
BASE_WORK_DIR = "fix_work"

# GPUを使うかどうか（True推奨）
USE_GPU = True

# 画質設定 (NVENCの -cq 値。小さいほど高画質。20前後が推奨)
QUALITY_CQ = 20
# ============================================

def get_duration(file_path):
    """動画の長さを秒単位で取得"""
    cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration", 
        "-of", "default=noprint_wrappers=1:nokey=1", file_path
    ]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return float(result.stdout.strip())
    except:
        return None

def get_safe_base_name(filename):
    """process_video.pyと同じロジックでファイル名を生成"""
    base_name = os.path.splitext(os.path.basename(filename))[0]
    # 記号を置換し、先頭20文字だけを使う（process_video.pyの仕様）
    safe_name = "".join([c if c.isalnum() or c in (' ', '.', '_', '-') else '_' for c in base_name])[:20]
    return safe_name

def fix_single_video(origin_path, chunk_files, output_path):
    """1つの動画ペアに対して修復処理を実行"""
    print(f"   ... {len(chunk_files)} 個のチャンクを結合中")

    # リスト作成
    list_txt = "temp_batch_list.txt"
    with open(list_txt, "w", encoding="utf-8") as f:
        for vid in chunk_files:
            abs_path = os.path.abspath(vid).replace("'", "'\\''")
            f.write(f"file '{abs_path}'\n")

    # 一時結合
    temp_concat = "temp_batch_concat.mp4"
    if os.path.exists(temp_concat): os.remove(temp_concat)
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_txt, 
        "-c", "copy", temp_concat
    ], stderr=subprocess.DEVNULL)

    # 時間計算
    duration_orig = get_duration(origin_path)
    duration_ai = get_duration(temp_concat)

    if not duration_orig or not duration_ai:
        print("   [Error] 動画の長さが取得できませんでした。スキップします。")
        return

    scale_factor = duration_orig / duration_ai
    print(f"   [Sync] 元: {duration_orig:.2f}s / AI: {duration_ai:.2f}s -> 倍率: {scale_factor:.4f}")

    # GPUエンコード設定
    if USE_GPU:
        enc_opts = ["-c:v", "h264_nvenc", "-cq", str(QUALITY_CQ), "-preset", "p6", "-tune", "hq"]
    else:
        enc_opts = ["-c:v", "libx264", "-crf", str(QUALITY_CQ), "-preset", "medium"]

    # 変換実行
    cmd = [
        "ffmpeg", "-y",
        "-i", temp_concat,       # [0] AI映像
        "-i", origin_path,       # [1] 元動画(音声)
        "-filter_complex", f"[0:v]setpts=PTS*{scale_factor}[v]", 
        "-map", "[v]",           
        "-map", "1:a?",          
        *enc_opts,
        "-c:a", "aac",           
        output_path
    ]

    try:
        subprocess.run(cmd, check=True, stderr=subprocess.DEVNULL)
        print(f"   ✅ 完了: {os.path.basename(output_path)}")
    except subprocess.CalledProcessError:
        print("   ❌ エンコード失敗")
    
    # 掃除
    if os.path.exists(list_txt): os.remove(list_txt)
    if os.path.exists(temp_concat): os.remove(temp_concat)


def main():
    # ディレクトリ設定
    origin_dir = os.path.join(BASE_WORK_DIR, "Origin")
    ainized_dir = os.path.join(BASE_WORK_DIR, "AInized")
    output_dir = os.path.join(BASE_WORK_DIR, "Fixed_Output")

    # 1. ディレクトリ自動作成
    for d in [origin_dir, ainized_dir, output_dir]:
        if not os.path.exists(d):
            os.makedirs(d)
            print(f"フォルダを作成しました: {d}")
    
    # ファイルがあるか確認
    origin_files = glob.glob(os.path.join(origin_dir, "*"))
    origin_files = [f for f in origin_files if f.lower().endswith(('.mp4', '.avi', '.mov', '.mkv'))]
    
    if not origin_files:
        print(f"\n⚠️ '{origin_dir}' に元動画が入っていません。動画を入れてから再実行してください。")
        return

    print(f"\n=== 全自動修復バッチ処理開始 (対象: {len(origin_files)}ファイル) ===\n")

    # 2. 元動画ごとにループ処理
    for i, origin_path in enumerate(origin_files):
        filename = os.path.basename(origin_path)
        print(f"[{i+1}/{len(origin_files)}] ターゲット: {filename}")

        # AIファイルを探すための「Safe Name」を計算
        safe_name = get_safe_base_name(filename)
        
        # AInizedフォルダから、この動画に関連するファイルを検索
        # パターン: safe_name + "_" + タイムスタンプ + "_part_" + ...
        search_pattern = os.path.join(ainized_dir, f"{safe_name}_*_part_*.mp4")
        found_chunks = glob.glob(search_pattern)

        if not found_chunks:
            print(f"   ⚠️ 対応するAI動画が見つかりません (検索名: {safe_name}...)")
            continue

        # 3. タイムスタンプごとにグループ分け (RunIDを抽出)
        # ファイル名例: video_20251218_120000_part_001.mp4
        # RunID: video_20251218_120000
        run_groups = {}
        for chunk in found_chunks:
            # "_part_" より前をRunIDとする
            if "_part_" in chunk:
                run_id = chunk.split("_part_")[0]
                if run_id not in run_groups:
                    run_groups[run_id] = []
                run_groups[run_id].append(chunk)
        
        # 4. 最新のRunIDを採用する
        # (RunIDには日付が入っているので、文字列ソートで最新がわかる)
        latest_run_id = sorted(run_groups.keys())[-1]
        target_chunks = sorted(run_groups[latest_run_id])
        
        print(f"   -> 発見: {len(target_chunks)}ファイル (RunID: {os.path.basename(latest_run_id)})")

        # 出力ファイル名
        fixed_filename = f"Fixed_{filename}"
        fixed_output_path = os.path.join(output_dir, fixed_filename)

        # 5. 修復実行
        fix_single_video(origin_path, target_chunks, fixed_output_path)

    print("\n=== 全ての処理が完了しました ===")
    print(f"確認フォルダ: {output_dir}")

if __name__ == "__main__":
    main()
