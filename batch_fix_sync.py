import os
import glob
import subprocess
import argparse
import shutil
import sys

# ================= 設定エリア =================
BASE_WORK_DIR = "fix_work"
# ============================================

sys.stdout.reconfigure(encoding='utf-8')

def get_safe_base_name(filename):
    base_name = os.path.splitext(os.path.basename(filename))[0]
    safe_name = "".join([c if c.isalnum() or c in (' ', '.', '_', '-') else '_' for c in base_name])[:20]
    return safe_name

# 正確な時間を取得（ストリームとコンテナ両方をチェック）
def get_exact_duration(file_path):
    # 1. まず映像ストリームの長さを取得
    cmd = [
        "ffprobe", "-v", "error", 
        "-select_streams", "v:0",
        "-show_entries", "stream=duration", 
        "-of", "default=noprint_wrappers=1:nokey=1", 
        file_path
    ]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, text=True)
        dur = float(res.stdout.strip())
        if dur > 0: return dur
    except:
        pass
    
    # 2. ダメならコンテナ全体の長さを取得
    cmd2 = [
        "ffprobe", "-v", "error", 
        "-show_entries", "format=duration", 
        "-of", "default=noprint_wrappers=1:nokey=1", 
        file_path
    ]
    try:
        res = subprocess.run(cmd2, stdout=subprocess.PIPE, text=True)
        return float(res.stdout.strip())
    except:
        return 0.0

def fix_single_video(origin_path, chunk_files, output_path):
    print(f"   ... {len(chunk_files)} 個のチャンクを結合中")

    # 1. 結合リスト作成
    list_txt = "temp_batch_list.txt"
    with open(list_txt, "w", encoding="utf-8") as f:
        for vid in chunk_files:
            abs_path = os.path.abspath(vid).replace("'", "'\\''")
            f.write(f"file '{abs_path}'\n")

    # 2. 一時結合（映像のみ）
    temp_concat = "temp_batch_concat.mp4"
    if os.path.exists(temp_concat): os.remove(temp_concat)
    
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_txt, 
        "-c", "copy", temp_concat
    ], stderr=subprocess.DEVNULL)

    # 3. 時間のズレを計算して伸縮倍率を決定
    duration_orig = get_exact_duration(origin_path)
    duration_ai = get_exact_duration(temp_concat)
    
    scale_factor = 1.0
    if duration_orig > 0 and duration_ai > 0:
        scale_factor = duration_orig / duration_ai
        print(f"   📏 Original: {duration_orig:.4f}s / AI: {duration_ai:.4f}s")
        print(f"   ⚡ Sync Correction: Stretching video by {scale_factor:.6f}x")
    else:
        print("   ⚠️ Duration check failed. Assuming 1.0x.")

    # 4. 強制同期合成 (Time-Stretch + Audio Replacement)
    cmd_final = [
        "ffmpeg", "-y",
        "-i", temp_concat,       # [0] AI映像
        "-i", origin_path,       # [1] 元動画(音声)
        "-filter_complex", f"[0:v]setpts=PTS*{scale_factor}[v]", 
        "-map", "[v]",           # 伸縮した映像
        "-map", "1:a?",          # 元の音声(絶対)
        "-c:v", "libx264",       # 再エンコード
        "-preset", "p5",            
        "-crf", "18",               
        "-c:a", "aac",           # 音声
        output_path
    ]

    # GPUエンコードチェック
    try:
        subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        cmd_final[cmd_final.index("-c:v") + 1] = "h264_nvenc"
    except:
        pass 

    try:
        subprocess.run(cmd_final, check=True, stderr=subprocess.DEVNULL)
        print(f"   ✅ 完了: {os.path.basename(output_path)}")
    except subprocess.CalledProcessError:
        print("   ❌ 合成失敗。単純コピーでリトライします。")
        # 失敗時のバックアップ
        subprocess.run([
            "ffmpeg", "-y", "-i", temp_concat, "-i", origin_path,
            "-map", "0:v", "-map", "1:a?", "-c", "copy", output_path
        ], stderr=subprocess.DEVNULL)
    
    # 掃除
    if os.path.exists(list_txt): os.remove(list_txt)
    if os.path.exists(temp_concat): os.remove(temp_concat)

def main():
    origin_dir = os.path.join(BASE_WORK_DIR, "Origin")
    ainized_dir = os.path.join(BASE_WORK_DIR, "AInized")
    output_dir = os.path.join(BASE_WORK_DIR, "Fixed_Output")

    for d in [origin_dir, ainized_dir, output_dir]:
        if not os.path.exists(d):
            os.makedirs(d)
            print(f"フォルダを作成しました: {d}")
    
    origin_files = glob.glob(os.path.join(origin_dir, "*"))
    origin_files = [f for f in origin_files if f.lower().endswith(('.mp4', '.avi', '.mov', '.mkv'))]
    
    if not origin_files:
        print(f"\n⚠️ '{origin_dir}' に元動画が入っていません。")
        print("1. 'fix_work/Origin' に音源となる元動画を入れてください。")
        print("2. 'fix_work/AInized' に生成された断片動画(_part_xxx.mp4)を入れてください。")
        return

    print(f"\n=== 全自動修復バッチ処理 (絶対時間同期モード) ===\n")

    for i, origin_path in enumerate(origin_files):
        filename = os.path.basename(origin_path)
        print(f"[{i+1}/{len(origin_files)}] ターゲット: {filename}")
        safe_name = get_safe_base_name(filename)
        
        # 緩い検索: ファイル名の一部が一致するものを探す
        search_pattern = os.path.join(ainized_dir, f"*{safe_name}*_part_*.mp4")
        found_chunks = glob.glob(search_pattern)
        
        # 見つからない場合、より緩く探す（タイムスタンプ部分を無視）
        if not found_chunks:
            # 元ファイル名の先頭10文字だけで探してみる
            short_name = safe_name[:10]
            search_pattern = os.path.join(ainized_dir, f"*{short_name}*_part_*.mp4")
            found_chunks = glob.glob(search_pattern)

        if not found_chunks:
            print(f"   ⚠️ 対応するAI動画が見つかりません: {safe_name}")
            continue

        # 複数の実行結果が混ざっている場合、最新のセットだけを使う
        run_groups = {}
        for chunk in found_chunks:
            if "_part_" in chunk:
                # パスからファイル名を取得し、_part_より前をIDとする
                base = os.path.basename(chunk)
                run_id = base.split("_part_")[0]
                if run_id not in run_groups: run_groups[run_id] = []
                run_groups[run_id].append(chunk)
        
        if not run_groups:
            print("   ⚠️ チャンクファイルの形式が一致しません。")
            continue

        # 一番新しい実行ID（文字列ソートで最後に来るもの＝日付が新しい）を選択
        latest_run_id = sorted(run_groups.keys())[-1]
        target_chunks = sorted(run_groups[latest_run_id])
        
        print(f"   -> 検出セット: {latest_run_id} ({len(target_chunks)} files)")

        fixed_filename = f"Fixed_{filename}"
        fixed_output_path = os.path.join(output_dir, fixed_filename)
        
        fix_single_video(origin_path, target_chunks, fixed_output_path)

    print("\n=== 全ての処理が完了しました ===")

if __name__ == "__main__":
    main()