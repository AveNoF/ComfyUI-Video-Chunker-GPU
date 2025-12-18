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

# ================= Configuration =================
COMFYUI_URL = "http://127.0.0.1:8188"
DEFAULT_WORKFLOW_FILE = "workflow_api.json"
CHUNK_SIZE = 1000           # Frames per chunk
MAX_PARALLEL_WORKERS = 1   # Number of parallel workers
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
    except Exception:
        return None

def wait_for_prompt_completion(prompt_id):
    while True:
        try:
            resp = requests.get(f"{COMFYUI_URL}/history/{prompt_id}")
            if resp.status_code == 200 and prompt_id in resp.json():
                return True
        except: pass
        time.sleep(2.0)

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
        print("[Error] FFmpeg merging failed. Please check if ffmpeg is installed.")

# ---------------------------------------------------------
# Worker Process
# ---------------------------------------------------------
def worker_process(video_path, workflow_file, start_frame, run_id):
    start_frame = int(start_frame)
    
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened(): sys.exit(1)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()

    chunk_index = start_frame // CHUNK_SIZE
    part_prefix = f"{run_id}_part_{chunk_index:03d}"
    
    # Check if already exists
    search_pattern = os.path.join(COMFYUI_OUTPUT_DIR, f"{part_prefix}*{OUTPUT_EXT}")
    if glob.glob(search_pattern):
        sys.exit(0)

    current_cap = min(CHUNK_SIZE, total_frames - start_frame)
    print(f"[Worker] Start Chunk {chunk_index} (Frame {start_frame} - {start_frame + current_cap})...")

    if not os.path.exists(workflow_file):
        sys.exit(1)

    with open(workflow_file, "r", encoding="utf-8") as f:
        workflow_template = json.load(f)

    workflow = workflow_template.copy()
    
    # Update workflow nodes
    if NODE_ID_LOADER in workflow:
        workflow[NODE_ID_LOADER]["inputs"]["frame_load_cap"] = current_cap
        workflow[NODE_ID_LOADER]["inputs"]["skip_first_frames"] = start_frame
        workflow[NODE_ID_LOADER]["inputs"]["video"] = os.path.basename(video_path)
    
    if NODE_ID_SAVER in workflow:
        workflow[NODE_ID_SAVER]["inputs"]["filename_prefix"] = part_prefix

    res = queue_prompt(workflow)
    if res:
        wait_for_prompt_completion(res['prompt_id'])
        print(f"[Worker] Chunk {chunk_index} FINISHED. Exiting.")
        sys.exit(0)
    
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

    # Generate Run ID
    base_name = os.path.splitext(os.path.basename(video_path))[0]
    safe_base_name = "".join([c if c.isalnum() or c in (' ', '.', '_', '-') else '_' for c in base_name])[:20]
    
    search_pattern = os.path.join(COMFYUI_OUTPUT_DIR, f"{safe_base_name}_*part_*{OUTPUT_EXT}")
    existing_files = glob.glob(search_pattern)
    existing_files.sort(key=os.path.getmtime)
    
    if existing_files:
        run_id = os.path.basename(existing_files[-1]).split("_part_")[0]
        print(f"Resuming Run ID: {run_id}")
    else:
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        run_id = f"{safe_base_name}_{timestamp}"
        print(f"New Run ID: {run_id}")

    # Create tasks
    tasks = []
    for i in range(0, total_frames, CHUNK_SIZE):
        tasks.append(i)

    running_procs = [] # [(process_obj, frame_index), ...]
    task_iter = iter(tasks)
    
    while True:
        # Clean up finished processes
        running_procs = [p for p in running_procs if p[0].poll() is None]
        
        # Spawn new workers
        while len(running_procs) < MAX_PARALLEL_WORKERS:
            try:
                next_start_frame = next(task_iter)
                
                # Skip check
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
                time.sleep(3) # Wait a bit to avoid API flooding
                
            except StopIteration:
                break 
        
        # Exit condition
        if not running_procs:
            try:
                # If iterator is empty, break
                next(iter(tasks)) 
                break 
            except:
                break 

        time.sleep(1)

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