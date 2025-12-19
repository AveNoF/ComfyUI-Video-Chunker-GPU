import os
import glob
import subprocess
import argparse
import shutil
import sys
import re

# ================= 設定エリア =================
BASE_WORK_DIR = "fix_work"
# ============================================

sys.stdout.reconfigure(encoding='utf-8')

def get_safe_base_name(filename):
    base_name = os.path.splitext(os.path.basename(filename))[0]
    safe_name = "".join([c if c.isalnum() or c in (' ', '.', '_', '-') else '_' for c in base_name])[:20]
    return safe_name

def get_exact_duration(file_path):
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
    except: pass
    
    cmd2 = [
        "ffprobe", "-v", "error", 
        "-show_entries", "format=duration", 
        "-of", "default=noprint_wrappers=1:nokey=1", 
        file_path
    ]
    try:
        res = subprocess.run(cmd2, stdout=subprocess.PIPE, text=True)
        return float(res.stdout.strip())
    except: return 0.0

# ★フレーム数を正確に数える関数
def count_frames_exact(file_path):
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-count_frames",
        "-show_entries", "stream=nb_read_frames",
        "-of", "default=noprint_wrappers=1:nokey=1",
        file_path
    ]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, text=True)
        frames = int(res.stdout.strip())
        if frames > 0: return frames
    except: pass
    return 0

def fix_single_video(origin_path, chunk_files, output_path):
    print(f"   ... Checking {len(chunk_files)} candidate files...")

    # 1. 重複排除ロジック
    chunk_map = {}
    pattern = re.compile(r"_part_(\d+)")
    
    for f_path in chunk_files:
        base = os.path.basename(f_path)
        match = pattern.search(base)
        if match:
            part_idx = int(match.group(1))
            if part_idx not in chunk_map:
                chunk_map[part_idx] = []
            chunk_map[part_idx].append(f_path)
    
    final_list = []
    sorted_indices = sorted(chunk_map.keys())
    
    for idx in sorted_indices:
        candidates = chunk_map[idx]
        if len(candidates) > 1:
            candidates.sort()
            selected = candidates[0]
            print(f"   ⚠️ Warning: Part {idx:03d} has duplicates! Using: {os.path.basename(selected)}")
            final_list.append(selected)
        else:
            final_list.append(candidates[0])

    if not final_list:
        print("   ❌ Valid chunks not found.")
        return

    print(f"   ✅ Merging {len(final_list)} unique chunks...")

    # 2. 結合リスト作成
    list_txt = "temp_batch_list.txt"
    with open(list_txt, "w", encoding="utf-8") as f:
        for vid in final_list:
            abs_path = os.path.abspath(vid).replace("'", "'\\''")
            f.write(f"file '{abs_path}'\n")

    # 3. 一時結合（映像のみ）
    temp_concat = "temp_batch_concat.mp4"
    if os.path.exists(temp_concat): os.remove(temp_concat)
    
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_txt, 
        "-c", "copy", temp_concat
    ], stderr=subprocess.DEVNULL)

    # 4. 強制リタイミング計算 (Total Frames / Original Duration)
    duration_orig = get_exact_duration(origin_path)
    total_frames = count_frames_exact(temp_concat)
    
    print(f"   📏 Original Duration: {duration_orig:.4f}s")
    print(f"   🎞️ Total AI Frames: {total_frames}")

    if duration_orig > 0 and total_frames > 0:
        # setpts = N * (DURATION / FRAMES) / TB
        # フレーム番号(N)に基づいて時間を再構築。PTSのズレを無視して均等配置する。
        retime_expr = f"N*({duration_orig}/{total_frames})/TB"
        print(f"   ⚡ Re-Timing: Force-distributing {total_frames} frames over {duration_orig}s")
    else:
        print("   ⚠️ Stat check failed. Using standard sync.")
        retime_expr = "PTS-STARTPTS"

    # 5. 強制同期合成
    cmd_final = [
        "ffmpeg", "-y",
        "-i", temp_concat,       # [0] AI映像
        "-i", origin_path,       # [1] 元動画(音声)
        "-filter_complex", f"[0:v]setpts={retime_expr}[v]", 
        "-map", "[v]",           
        "-map", "1:a?",          # 元の音声(絶対)
        "-c:v", "libx264",       # 再エンコード
        "-preset", "p5",            
        "-crf", "18",               
        "-fps_mode", "passthrough", # 勝手なフレーム削除を防ぐ
        "-c:a", "aac",           
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

    print(f"\n=== 全自動修復バッチ処理 (強制リタイミングモード) ===\n")

    for i, origin_path in enumerate(origin_files):
        filename = os.path.basename(origin_path)
        print(f"[{i+1}/{len(origin_files)}] ターゲット: {filename}")
        safe_name = get_safe_base_name(filename)
        
        search_pattern = os.path.join(ainized_dir, f"*{safe_name}*_part_*.mp4")
        found_chunks = glob.glob(search_pattern)
        
        if not found_chunks:
            short_name = safe_name[:10]
            search_pattern = os.path.join(ainized_dir, f"*{short_name}*_part_*.mp4")
            found_chunks = glob.glob(search_pattern)

        if not found_chunks:
            print(f"   ⚠️ 対応するAI動画が見つかりません: {safe_name}")
            continue

        run_groups = {}
        for chunk in found_chunks:
            base = os.path.basename(chunk)
            match = re.match(r"(.+)_part_\d+", base)
            if match:
                run_id = match.group(1)
                if run_id not in run_groups: run_groups[run_id] = []
                run_groups[run_id].append(chunk)
        
        if not run_groups:
            print("   ⚠️ ファイル名の形式が一致しません。")
            continue

        latest_run_id = sorted(run_groups.keys())[-1]
        target_chunks = sorted(run_groups[latest_run_id])
        
        print(f"   -> 検出セットID: {latest_run_id}")

        fixed_filename = f"Fixed_{filename}"
        fixed_output_path = os.path.join(output_dir, fixed_filename)
        
        fix_single_video(origin_path, target_chunks, fixed_output_path)

    print("\n=== 全ての処理が完了しました ===")

if __name__ == "__main__":
    main()