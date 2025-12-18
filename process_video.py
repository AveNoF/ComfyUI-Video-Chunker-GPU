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
CHUNK_SIZE = 1000           # ★安全のため100に戻しました
MAX_PARALLEL_WORKERS = 1   # デバッグ中は1推奨
OUTPUT_EXT = ".mp4"
NODE_ID_LOADER = "1"       # Node ID for VHS_LoadVideo
NODE_ID_SAVER = "4"        # Node ID for VHS_VideoCombine
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
        print("Make sure ComfyUI is running!")
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

def merge_videos(file_list, output_filename):
    print(f"\n=== Merging {len(file_list)} parts into {output_filename} ===")
    list_txt = "concat_list.txt"
    with open(list_txt, "w", encoding="utf-8") as f:
        for vid in file_list:
            safe_vid = vid.replace("'", "'\\''")
            f.write(f"file '{safe_vid}'\n")

    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_txt, "-c", "copy", output_filename]
    try:
        subprocess.run(cmd, check=True, stderr=subprocess.DEVNULL)
        print(f"Success! Saved to: {output_filename}")
        os.remove(list_txt)
    except subprocess.CalledProcessError:
        print("[Error] FFmpeg merging failed.")

# ---------------------------------------------------------
# Worker Process
# ---------------------------------------------------------
def worker_process(video_path, workflow_file, start_frame, run_id):
    try:
        start_frame = int(start_frame)
        
        # 1. 動画が開けるかチェック
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            print(f"[Error] CV2 Cannot open video: {video_path}")
            sys.exit(1)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        cap.release()

        chunk_index = start_frame // CHUNK_SIZE
        part_prefix = f"{run_id}_part_{chunk_index:03d}"
        
        # 既に完了しているかチェック
        search_pattern = os.path.join(COMFYUI_OUTPUT_DIR, f"{part_prefix}*{OUTPUT_EXT}")
        if glob.glob(search_pattern):
            sys.exit(0)

        current_cap = min(CHUNK_SIZE, total_frames - start_frame)
        print(f"[Worker] Start Chunk {chunk_index} (Frame {start_frame} - {start_frame + current_cap})...")

        if not os.path.exists(workflow_file):
            print(f"[Error] Workflow file not found: {workflow_file}")
            sys.exit(1)

        with open(workflow_file, "r", encoding="utf-8") as f:
            workflow_template = json.load(f)

        workflow = workflow_template.copy()
        
        # 2. ノードIDの存在チェック
        if NODE_ID_LOADER not in workflow:
            print(f"[Error] Node ID {NODE_ID_LOADER} (Loader) not found in workflow!")
            sys.exit(1)
        if NODE_ID_SAVER not in workflow:
            print(f"[Error] Node ID {NODE_ID_SAVER} (Saver) not found in workflow!")
            sys.exit(1)

        # Update workflow nodes
        workflow[NODE_ID_LOADER]["inputs"]["frame_load_cap"] = current_cap
        workflow[NODE_ID_LOADER]["inputs"]["skip_first_frames"] = start_frame
        # 絶対パスに変換
        workflow[NODE_ID_LOADER]["inputs"]["video"] = os.path.abspath(video_path)
        
        workflow[NODE_ID_SAVER]["inputs"]["filename_prefix"] = part_prefix

        # 3. ComfyUIに送信
        res = queue_prompt(workflow)
        if res and 'prompt_id' in res:
            wait_for_prompt_completion(res['prompt_id'])
            print(f"[Worker] Chunk {chunk_index} FINISHED.")
            sys.exit(0)
        else:
            print("[Error] Failed to get prompt_id from ComfyUI.")
            sys.exit(1)

    except Exception:
        print("!!! Exception in Worker Process !!!")
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
    cap.release()
    print(f"Total Frames: {total_frames} / Chunk Size: {CHUNK_SIZE}")

    base_name = os.path.splitext(os.path.basename(video_path))[0]
    safe_base_name = "".join([c if c.isalnum() or c in (' ', '.', '_', '-') else '_' for c in base_name])[:20]
    
    # RunID生成
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
        # 1. 終了したプロセスをチェック
        # poll() が None でなければ終了している
        for p, frame in running_procs[:]:
            if p.poll() is not None:
                # 終了コードを確認
                if p.returncode != 0:
                    print(f"\n[CRITICAL] Worker for frame {frame} failed with exit code {p.returncode}!")
                    print("Stopping all processing to prevent further errors.")
                    error_occurred = True
                    break
                running_procs.remove((p, frame))
        
        if error_occurred:
            break

        # 2. 新しいWorkerを投入
        while len(running_procs) < MAX_PARALLEL_WORKERS:
            try:
                next_start_frame = next(task_iter)
                
                # 既にファイルがあるか簡易チェック（高速化のためManager側でもやる）
                chunk_index = next_start_frame // CHUNK_SIZE
                part_prefix = f"{run_id}_part_{chunk_index:03d}"
                if glob.glob(os.path.join(COMFYUI_OUTPUT_DIR, f"{part_prefix}*{OUTPUT_EXT}")):
                    print(f"Chunk {chunk_index} exists. Skipping.")
                    continue

                cmd = [sys.executable, __file__, video_path, workflow_file, 
                       "--worker_mode", 
                       "--start_frame", str(next_start_frame), 
                       "--run_id", run_id]
                
                # subprocessを実行
                proc = subprocess.Popen(cmd)
                running_procs.append((proc, next_start_frame))
                time.sleep(2) 
                
            except StopIteration:
                break 
        
        # 3. 終了判定
        if not running_procs:
            try:
                next(iter(tasks)) # タスクが残っているか確認
                break # 残ってなければ終了
            except:
                break # イテレータが空なら終了

        time.sleep(1)

    if error_occurred:
        print("\n❌ Processing failed due to worker errors. Check the logs above.")
        sys.exit(1)

    print("\n>>> All chunks completed!")

    # Merge
    all_files = glob.glob(os.path.join(COMFYUI_OUTPUT_DIR, f"{run_id}_part_*{OUTPUT_EXT}"))
    all_files.sort()
    
    if all_files:
        final_output_name = f"{run_id}_merged{OUTPUT_EXT}"
        merge_videos(all_files, os.path.join(COMFYUI_OUTPUT_DIR, final_output_name))

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
