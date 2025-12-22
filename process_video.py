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

def merge_videos_simple(file_list, output_filename, original_video_path):
    print(f"\n=== Merging {len(file_list)} files (Simple Mode) ===")
    
    chunk_map = {}
    pattern = re.compile(r"_part_(\d+)")
    
    for f_path in file_list:
        base = os.path.basename(f_path)
        match = pattern.search(base)
        if match:
            part_idx = int(match.group(1))
            if part_idx not in chunk_map: chunk_map[part_idx] = []
            chunk_map[part_idx].append(f_path)
    
    final_list = []
    sorted_indices = sorted(chunk_map.keys())
    
    for idx in sorted_indices:
        candidates = chunk_map[idx]
        if len(candidates) > 1:
            candidates.sort()
            selected = candidates[0]
            if len(candidates) > 1:
                print(f"⚠️ Warning: Part {idx:03d} has duplicates! Using: {os.path.basename(selected)}")
            final_list.append(selected)
        else:
            final_list.append(candidates[0])

    list_txt = "concat_list.txt"
    with open(list_txt, "w", encoding="utf-8") as f:
        for vid in final_list:
            safe_vid = os.path.abspath(vid).replace("'", "'\\''")
            f.write(f"file '{safe_vid}'\n")

    cmd_final = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", list_txt, 
        "-i", original_video_path,                    
        "-map", "0:v",                                
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

def worker_process(video_path, workflow_file, start_frame, run_id):
    try:
        start_frame = int(start_frame)
        cap = cv2.VideoCapture(video_path)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        
        # 30fps強制 (重要)
        target_fps = 30.0 
        
        cap.release()

        chunk_index = start_frame // CHUNK_SIZE
        part_prefix = f"{run_id}_part_{chunk_index:03d}"
        
        # ★ ここで「すでにファイルがあるか」をチェックして、あれば終了(スキップ)する
        search_pattern = os.path.join(COMFYUI_OUTPUT_DIR, f"{part_prefix}*{OUTPUT_EXT}")
        existing_files = glob.glob(search_pattern)
        
        # 0バイトの破損ファイルでなければスキップ
        if existing_files:
            if os.path.getsize(existing_files[0]) > 1024:
                print(f"[Worker] Chunk {chunk_index}: ✅ Exists. Skipping.")
                sys.exit(0)
            else:
                 print(f"[Worker] Chunk {chunk_index}: ⚠️ Found empty file, regenerating.")

        current_cap = min(CHUNK_SIZE, total_frames - start_frame)
        print(f"[Worker] Chunk {chunk_index}: Generating {current_cap} frames...")

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
    print(f"=== Manager Started (Processing CFR Source) ===")
    cap = cv2.VideoCapture(original_video_path)
    if not cap.isOpened(): return
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    
    base_name = os.path.splitext(os.path.basename(original_video_path))[0]
    safe_base_name = "".join([c if c.isalnum() or c in (' ', '.', '_', '-') else '_' for c in base_name])[:20]
    
    # ★★★ ここが変更点: 既存のRun IDを探す ★★★
    # outputフォルダ内に、この動画の名前で始まる断片があるか探す
    search_pattern = os.path.join(COMFYUI_OUTPUT_DIR, f"{safe_base_name}*_part_*{OUTPUT_EXT}")
    existing_chunks = glob.glob(search_pattern)
    
    if existing_chunks:
        # 見つかった場合、最新のファイルのIDを再利用する
        latest_file = max(existing_chunks, key=os.path.getctime)
        # ファイル名からRun ID (日付入り) を抽出
        # 例: SafeName_20231222_120000_part_001.mp4 -> SafeName_20231222_120000
        match = re.match(r"(.+)_part_\d+", os.path.basename(latest_file))
        if match:
            run_id = match.group(1)
            print(f"🔄 Resuming existing run: {run_id}")
            print(f"   (Existing chunks will be skipped)")
        else:
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            run_id = f"{safe_base_name}_{timestamp}"
    else:
        # 新規作成
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
                
                # ワーカー起動前にマネージャー側でも簡易チェック（高速化のため）
                part_prefix = f"{run_id}_part_{chunk_index:03d}"
                if glob.glob(os.path.join(COMFYUI_OUTPUT_DIR, f"{part_prefix}*{OUTPUT_EXT}")):
                    print(f"[Manager] Chunk {chunk_index} exists. Skipping spawn.")
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
        all_files = glob.glob(os.path.join(COMFYUI_OUTPUT_DIR, f"{run_id}_part_*{OUTPUT_EXT}"))
        all_files.sort()
        
        if all_files:
            final_output_name = f"{run_id}_merged{OUTPUT_EXT}"
            merge_videos_simple(all_files, os.path.join(COMFYUI_OUTPUT_DIR, final_output_name), original_video_path)

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