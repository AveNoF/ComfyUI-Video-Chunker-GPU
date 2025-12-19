import os
import glob
import subprocess
import argparse
import shutil

# ================= 設定エリア =================
BASE_WORK_DIR = "fix_work"
# ============================================

def get_duration(file_path):
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
    base_name = os.path.splitext(os.path.basename(filename))[0]
    safe_name = "".join([c if c.isalnum() or c in (' ', '.', '_', '-') else '_' for c in base_name])[:20]
    return safe_name

def fix_single_video(origin_path, chunk_files, output_path):
    print(f"   ... {len(chunk_files)} 個のチャンクを結合中")

    # 1. 結合リスト作成
    list_txt = "temp_batch_list.txt"
    with open(list_txt, "w", encoding="utf-8") as f:
        for vid in chunk_files:
            abs_path = os.path.abspath(vid).replace("'", "'\\''")
            f.write(f"file '{abs_path}'\n")

    # 2. 一時結合
    temp_concat = "temp_batch_concat.mp4"
    if os.path.exists(temp_concat): os.remove(temp_concat)
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_txt, 
        "-c", "copy", temp_concat
    ], stderr=subprocess.DEVNULL)

    # 3. 音声合成 (Simple Copy)
    # 難しいことはせず、映像そのままで音だけ乗せる
    cmd = [
        "ffmpeg", "-y",
        "-i", temp_concat,       
        "-i", origin_path,       
        "-map", "0:v",           
        "-map", "1:a?",          
        "-c:v", "copy",          
        "-c:a", "aac",           
        "-shortest",
        output_path
    ]

    try:
        subprocess.run(cmd, check=True, stderr=subprocess.DEVNULL)
        print(f"   ✅ 完了: {os.path.basename(output_path)}")
    except subprocess.CalledProcessError:
        print("   ❌ 合成失敗")
    
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
        return

    print(f"\n=== 全自動修復バッチ処理開始 (FPS維持モード) ===\n")

    for i, origin_path in enumerate(origin_files):
        filename = os.path.basename(origin_path)
        print(f"[{i+1}/{len(origin_files)}] ターゲット: {filename}")
        safe_name = get_safe_base_name(filename)
        search_pattern = os.path.join(ainized_dir, f"{safe_name}_*_part_*.mp4")
        found_chunks = glob.glob(search_pattern)

        if not found_chunks:
            print(f"   ⚠️ 対応するAI動画が見つかりません: {safe_name}")
            continue

        run_groups = {}
        for chunk in found_chunks:
            if "_part_" in chunk:
                run_id = chunk.split("_part_")[0]
                if run_id not in run_groups: run_groups[run_id] = []
                run_groups[run_id].append(chunk)
        
        latest_run_id = sorted(run_groups.keys())[-1]
        target_chunks = sorted(run_groups[latest_run_id])
        
        fixed_filename = f"Fixed_{filename}"
        fixed_output_path = os.path.join(output_dir, fixed_filename)
        fix_single_video(origin_path, target_chunks, fixed_output_path)

    print("\n=== 全ての処理が完了しました ===")

if __name__ == "__main__":
    main()