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

# ================= Configuration =================
COMFYUI_URL = "http://127.0.0.1:8188"
DEFAULT_WORKFLOW_FILE = "workflow_api.json"
CHUNK_SIZE = 1000          
MAX_PARALLEL_WORKERS = 1   
OUTPUT_EXT = ".mp4"
NODE_ID_LOADER = "1"       
NODE_ID_SAVER = "4"        
# ============================================

# Auto-detect output directory based on OS
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
    except requests.exceptions.ConnectionError:
        print(f"\n[Fatal Error] Cannot connect to ComfyUI at {COMFYUI_URL}")
        return None
    except Exception as e:
        print(f"\n[Fatal Error] Failed to queue prompt: {e}")
        return None

def wait_for_prompt_completion(prompt_id):
    while True:
        try:
            resp = requests.get(f"{COMFYUI_URL}/history/{prompt_id}")
            if resp.status_code == 200:
                history = resp.json()
                if prompt_id in history:
                    return True
        except: pass
        time.sleep(1.0)

# ★★★ 修正箇所: FPSを強力に矯正する ★★★
def merge_videos_with_audio(file_list, output_filename, original_video_path, fps):
    print(f"\n=== Merging {len(file_list)} parts (Target FPS: {fps}) ===")
    
    # 1. まず映像だけを結合（一時ファイル）
    temp_concat = output_filename.replace(".mp4", "_temp_concat.mp4")
    list_txt = "concat_list.txt"
    
    with open(list_txt, "w", encoding="utf-8") as f:
        for vid in file_list:
            safe_vid = vid.replace("'", "'\\''")
            f.write(f"file '{safe_vid}'\n")

    print("Step 1: Concatenating video chunks...")
    cmd_concat = [
        "ffmpeg", "-y", 
        "-f", "concat", 
        "-safe", "0", 
        "-i", list_txt, 
        "-c", "copy", 
        temp_concat
    ]
    
    try:
        subprocess.run(cmd_concat, check=True, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        print("[Error] Video concatenation failed.")
        if os.path.exists(list_txt): os.remove(list_txt)
        return

    # 2. 元動画から音声を移植し、FPSタグを書き換える（再エンコードなしで速度調整）
    # ここで -r {fps} を入力側と出力側に明示することで速度ズレを直す
    print(f"Step 2: Muxing audio & Correcting FPS to {fps}...")
    
    cmd_mux = [
        "ffmpeg", "-y",
        "-r", str(fps),             # ★重要: 入力映像をこのFPSとして読み込む
        "-i", temp_concat,          # 映像: 結合したAI動画
        "-i", original_video_path,  # 音声: 元の動画
        "-map", "0:v",              # 映像トラック
        "-map", "1:a?",             # 音声トラック
        "-c:v", "copy",             # 映像は再エンコードなし（高速）
        "-c:a", "aac",              # 音声はAAC
        "-shortest",                # 短い方に合わせる
        "-r", str(fps),             # ★重要: 出力もこのFPSにする
        output_filename
    ]

    try:
        subprocess.run(cmd_mux, check=True, stderr=subprocess.DEVNULL)
        print(f"Success! Saved to: {output_filename}")
    except subprocess.CalledProcessError:
        print("[Error] Audio muxing failed. Trying alternative method...")
        # 失敗した場合、再エンコードで無理やり合わせるモード
        cmd_force = [
            "ffmpeg", "-y",
            "-i", temp_concat,
            "-i", original_video_path,
            "-map", "0:v", "-map", "1:a?",
            "-vf", f"fps={fps}",    # フィルタで強制変換
            "-c:a", "aac",
            "-shortest",
            output_filename
        ]
        subprocess.run(cmd_force, check=True, stderr=subprocess.DEVNULL)
        print(f"Success! (Forced Re-encode) Saved to: {output_filename}")
    
    # お掃除
    if os.path.exists(list_txt): os.remove(list_txt)
    if os.path.exists(temp_concat): os.remove(temp_concat)


# ---------------------------------------------------------
# Worker Process
# ---------------------------------------------------------
def worker_process(video_path, workflow_file, start_frame, run_id):
    try:
        start_frame = int(start_frame)
        
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            print(f"[Error] CV2 Cannot open video: {video_path}")
            sys.exit(1)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        cap.release()

        chunk_index = start_frame // CHUNK_SIZE
        part_prefix = f"{run_id}_part_{chunk_index:03d}"
        
        search_pattern = os.path.join(COMFYUI_OUTPUT_DIR, f"{part_prefix}*{OUTPUT_EXT}")
        if glob.glob(search_pattern):
            sys.exit(0)

        current_cap = min(CHUNK_SIZE, total_frames - start_frame)
        print(f"[Worker] Start Chunk {chunk_index} (Frame {start_frame} - {start_frame + current_cap})...")

        with open(workflow_file, "r", encoding="utf-8") as f:
            workflow_template = json.load(f)

        workflow = workflow_template.copy()
        
        if NODE_ID_LOADER not in workflow or NODE_ID_SAVER not in workflow:
            print(f"[Error] Node IDs not found in workflow!")
            sys.exit(1)

        workflow[NODE_ID_LOADER]["inputs"]["frame_load_cap"] = current_cap
        workflow[NODE_ID_LOADER]["inputs"]["skip_first_frames"] = start_frame
        workflow[NODE_ID_LOADER]["inputs"]["video"] = os.path.abspath(video_path)
        workflow[NODE_ID_SAVER]["inputs"]["filename_prefix"] = part_prefix

        res = queue_prompt(workflow)
        if res and 'prompt_id' in res:
            wait_for_prompt_completion(res['prompt_id'])
            print(f"[Worker] Chunk {chunk_index} FINISHED.")
            sys.exit(0)
        else:
            sys.exit(1)

    except Exception:
        traceback.print_exc()
        sys.exit(1)

# ---------------------------------------------------------
# Manager Process
# ---------------------------------------------------------
def manager_process(video_path, workflow_file):
    print(f"=== Manager Started: Parallel Mode ({MAX_PARALLEL_WORKERS} workers) ===")
    
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"[Error] Cannot open video: {video_path}")
        return
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) # FPS取得
    cap.release()
    
    print(f"Total Frames: {total_frames} / FPS: {fps:.2f} / Chunk Size: {CHUNK_SIZE}")

    base_name = os.path.splitext(os.path.basename(video_path))[0]
    safe_base_name = "".join([c if c.isalnum() or c in (' ', '.', '_', '-') else '_' for c in base_name])[:20]
    
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    run_id = f"{safe_base_name}_{timestamp}"
    print(f"New Run ID: {run_id}")

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
                    print(f"\n[CRITICAL] Worker failed!")
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
                    print(f"Chunk {chunk_index} exists. Skipping.")
                    continue

                cmd = [sys.executable, __file__, video_path, workflow_file, 
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

    if error_occurred:
        sys.exit(1)

    print("\n>>> All chunks completed!")

    all_files = glob.glob(os.path.join(COMFYUI_OUTPUT_DIR, f"{run_id}_part_*{OUTPUT_EXT}"))
    all_files.sort()
    
    if all_files:
        final_output_name = f"{run_id}_merged{OUTPUT_EXT}"
        # マージ実行
        merge_videos_with_audio(all_files, os.path.join(COMFYUI_OUTPUT_DIR, final_output_name), video_path, fps)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("video_path", nargs="?", help="Path to video file")
    parser.add_argument("workflow_file", nargs="?", default=DEFAULT_WORKFLOW_FILE, help="Path to workflow json")
    parser.add_argument("--worker_mode", action="store_true", help="Internal flag for worker process")
    parser.add_argument("--start_frame", help="Start frame for worker")
    parser.add_argument("--run_id", help="Run ID for filename")
    
    args = parser.parse_args()

    if not args.video_path:
        if args.worker_mode: sys.exit(1)
        try:
            input_path = input("Enter video file path: ").strip().strip("'").strip('"')
            if not input_path: sys.exit(0)
            args.video_path = input_path
        except EOFError: sys.exit(0)

    if args.worker_mode:
        if not args.start_frame or not args.run_id:
            sys.exit(1)
        worker_process(args.video_path, args.workflow_file, args.start_frame, args.run_id)
    else:
        manager_process(args.video_path, args.workflow_file)