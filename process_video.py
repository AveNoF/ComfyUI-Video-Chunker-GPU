import json
import requests
import cv2
import os
import time
import sys
import subprocess
import datetime
import glob
import argparse
import traceback
import re

# ================= Configuration =================
COMFYUI_URL = "http://127.0.0.1:8188"
DEFAULT_WORKFLOW_FILE = "workflow_api.json"
CHUNK_SIZE = 1000          
MAX_PARALLEL_WORKERS = 1   
OUTPUT_EXT = ".mp4"
NODE_ID_LOADER = "1"       
NODE_ID_SAVER = "4"        
# ============================================

USER_HOME = os.path.expanduser("~")
COMFYUI_OUTPUT_DIR = os.path.join(USER_HOME, "ComfyUI", "output")
sys.stdout.reconfigure(encoding='utf-8')

def queue_prompt(workflow):
    p = {"prompt": workflow}
    data = json.dumps(p).encode('utf-8')
    try:
        resp = requests.post(f"{COMFYUI_URL}/prompt", data=data)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"\n[Fatal Error] Failed to queue prompt: {e}")
        return None

def wait_for_prompt_completion(prompt_id):
    while True:
        try:
            resp = requests.get(f"{COMFYUI_URL}/history/{prompt_id}")
            if resp.status_code == 200:
                if prompt_id in resp.json():
                    return True
        except: pass
        time.sleep(1.0)

def get_exact_duration(file_path):
    cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=duration", "-of", "default=noprint_wrappers=1:nokey=1", file_path]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, text=True)
        dur = float(res.stdout.strip())
        if dur > 0: return dur
    except: pass
    return 0.0

# ★重複排除ロジックを追加した結合関数
def merge_videos_unique(file_list, output_filename, original_video_path):
    print(f"\n=== Merging {len(file_list)} files (Filtering Duplicates...) ===")
    
    # 1. 重複チェック: パート番号ごとにファイルを整理
    # ファイル名から "part_XXX" を抜き出す
    chunk_map = {}
    pattern = re.compile(r"_part_(\d+)")
    
    for f_path in file_list:
        base = os.path.basename(f_path)
        match = pattern.search(base)
        if match:
            part_idx = int(match.group(1))
            if part_idx not in chunk_map:
                chunk_map[part_idx] = []
            chunk_map[part_idx].append(f_path)
    
    # 2. 各パートから「1つだけ」選ぶ
    # (もし複数あったら、ファイル名が短い方=余計な接尾辞がない方を選ぶ、などのルールで統一)
    final_list = []
    sorted_indices = sorted(chunk_map.keys())
    
    for idx in sorted_indices:
        candidates = chunk_map[idx]
        if len(candidates) > 1:
            # 重複発見！警告を出して1つに絞る
            # ここでは「ファイルサイズが大きい方」あるいは「単純にソートして先頭」を選ぶ
            # 通常、ComfyUIは連番をつけるので、一番若い番号を採用するのが安全
            candidates.sort() 
            selected = candidates[0]
            print(f"⚠️ Warning: Part {idx:03d} has duplicates! Using: {os.path.basename(selected)}")
            print(f"   (Ignored: {[os.path.basename(c) for c in candidates if c != selected]})")
            final_list.append(selected)
        else:
            final_list.append(candidates[0])

    print(f"✅ Final List: {len(final_list)} unique chunks.")

    # 3. 結合リスト作成
    temp_concat = output_filename.replace(".mp4", "_temp_concat.mp4")
    list_txt = "concat_list.txt"
    with open(list_txt, "w", encoding="utf-8") as f:
        for vid in final_list:
            safe_vid = vid.replace("'", "'\\''")
            f.write(f"file '{safe_vid}'\n")

    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_txt, 
        "-c", "copy", temp_concat
    ], stderr=subprocess.DEVNULL)

    # 4. 時間同期処理
    duration_orig = get_exact_duration(original_video_path)
    duration_ai = get_exact_duration(temp_concat)
    
    scale_factor = 1.0
    if duration_orig > 0 and duration_ai > 0:
        scale_factor = duration_orig / duration_ai
        print(f"   Original: {duration_orig:.4f}s / AI: {duration_ai:.4f}s (Scale: {scale_factor:.6f})")

    cmd_final = [
        "ffmpeg", "-y",
        "-i", temp_concat,          
        "-i", original_video_path,  
        "-filter_complex", f"[0:v]setpts=PTS*{scale_factor}[v]", 
        "-map", "[v]",              
        "-map", "1:a?",             
        "-c:v", "libx264",          
        "-preset", "p5",            
        "-crf", "18",               
        "-c:a", "aac",              
        output_filename
    ]
    
    try:
        subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        cmd_final[cmd_final.index("-c:v") + 1] = "h264_nvenc"
    except: pass 

    try:
        subprocess.run(cmd_final, check=True, stderr=subprocess.DEVNULL)
        print(f"✅ Success! Saved to: {output_filename}")
    except:
        print("❌ Merge failed.")

    if os.path.exists(list_txt): os.remove(list_txt)
    if os.path.exists(temp_concat): os.remove(temp_concat)

def worker_process(video_path, workflow_file, start_frame, run_id):
    try:
        start_frame = int(start_frame)
        cap = cv2.VideoCapture(video_path)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        target_fps = 60.0 
        cap.release()

        chunk_index = start_frame // CHUNK_SIZE
        part_prefix = f"{run_id}_part_{chunk_index:03d}"
        
        # 重複チェック: 既にどれか1つでもあればスキップ
        search_pattern = os.path.join(COMFYUI_OUTPUT_DIR, f"{part_prefix}*{OUTPUT_EXT}")
        if glob.glob(search_pattern):
            sys.exit(0)

        current_cap = min(CHUNK_SIZE, total_frames - start_frame)
        print(f"[Worker] Chunk {chunk_index}: {current_cap} frames...")

        with open(workflow_file, "r", encoding="utf-8") as f:
            workflow = json.load(f)

        if NODE_ID_LOADER in workflow:
            workflow[NODE_ID_LOADER]["inputs"]["frame_load_cap"] = current_cap
            workflow[NODE_ID_LOADER]["inputs"]["skip_first_frames"] = start_frame
            workflow[NODE_ID_LOADER]["inputs"]["video"] = os.path.abspath(video_path)

        if NODE_ID_SAVER in workflow:
            workflow[NODE_ID_SAVER]["inputs"]["filename_prefix"] = part_prefix
            workflow[NODE_ID_SAVER]["inputs"]["frame_rate"] = target_fps 

        res = queue_prompt(workflow)
        if res and 'prompt_id' in res:
            wait_for_prompt_completion(res['prompt_id'])
            sys.exit(0)
        else:
            sys.exit(1)

    except Exception:
        traceback.print_exc()
        sys.exit(1)

def manager_process(original_video_path, workflow_file):
    print(f"=== Manager Started: No-Duplicate Mode ===")
    cap = cv2.VideoCapture(original_video_path)
    if not cap.isOpened(): return
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    
    base_name = os.path.splitext(os.path.basename(original_video_path))[0]
    safe_base_name = "".join([c if c.isalnum() or c in (' ', '.', '_', '-') else '_' for c in base_name])[:20]
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    run_id = f"{safe_base_name}_{timestamp}"

    tasks = []
    for i in range(0, total_frames, CHUNK_SIZE):
        tasks.append(i)

    running_procs = [] 
    task_iter = iter(tasks)
    error_occurred = False

    while True:
        for p, frame in running_procs[:]:
            if p.poll() is not None:
                if p.returncode != 0:
                    error_occurred = True
                    break
                running_procs.remove((p, frame))
        
        if error_occurred: break

        while len(running_procs) < MAX_PARALLEL_WORKERS:
            try:
                next_start_frame = next(task_iter)
                chunk_index = next_start_frame // CHUNK_SIZE
                part_prefix = f"{run_id}_part_{chunk_index:03d}"
                if glob.glob(os.path.join(COMFYUI_OUTPUT_DIR, f"{part_prefix}*{OUTPUT_EXT}")):
                    continue

                cmd = [sys.executable, __file__, original_video_path, workflow_file, 
                       "--worker_mode", 
                       "--start_frame", str(next_start_frame), 
                       "--run_id", run_id]
                proc = subprocess.Popen(cmd)
                running_procs.append((proc, next_start_frame))
                time.sleep(2) 
            except StopIteration:
                break 
        
        if not running_procs:
            try:
                next(iter(tasks))
                break 
            except:
                break 
        time.sleep(1)

    if not error_occurred:
        print("\n>>> All chunks completed!")
        # 緩い条件でファイルを集める（重複含む）
        all_files = glob.glob(os.path.join(COMFYUI_OUTPUT_DIR, f"{run_id}_part_*{OUTPUT_EXT}"))
        all_files.sort()
        
        if all_files:
            final_output_name = f"{run_id}_merged{OUTPUT_EXT}"
            # ★ここで重複排除しながら結合！
            merge_videos_unique(all_files, os.path.join(COMFYUI_OUTPUT_DIR, final_output_name), original_video_path)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("video_path", nargs="?", help="Path to video file")
    parser.add_argument("workflow_file", nargs="?", default=DEFAULT_WORKFLOW_FILE, help="Path to workflow json")
    parser.add_argument("--worker_mode", action="store_true")
    parser.add_argument("--start_frame")
    parser.add_argument("--run_id")
    args = parser.parse_args()

    if not args.video_path:
        if args.worker_mode: sys.exit(1)
        try:
            input_path = input("Enter video file path: ").strip().strip("'").strip('"')
            if not input_path: sys.exit(0)
            args.video_path = input_path
        except: sys.exit(0)

    if args.worker_mode:
        worker_process(args.video_path, args.workflow_file, args.start_frame, args.run_id)
    else:
        manager_process(args.video_path, args.workflow_file)