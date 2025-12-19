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
CHUNK_SIZE = 1000          # チャンクサイズ（小さいほど安全）
MAX_PARALLEL_WORKERS = 1   # 並列数（基本は1でOK）
OUTPUT_EXT = ".mp4"
NODE_ID_LOADER = "1"       # VHS_LoadVideoのノードID
NODE_ID_SAVER = "4"        # VHS_VideoCombineのノードID
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

# シンプルな結合と音声合成（FPS変更なし・再エンコードなし）
def merge_videos_exact_fps(file_list, output_filename, original_video_path, fps):
    print(f"\n=== Merging {len(file_list)} parts (Original FPS: {fps}) ===")
    
    # 1. 結合用リスト作成
    temp_concat = output_filename.replace(".mp4", "_temp.mp4")
    list_txt = "concat_list.txt"
    
    with open(list_txt, "w", encoding="utf-8") as f:
        for vid in file_list:
            safe_vid = vid.replace("'", "'\\''")
            f.write(f"file '{safe_vid}'\n")

    # 2. 単純結合
    cmd_concat = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_txt, 
        "-c", "copy", temp_concat
    ]
    try:
        subprocess.run(cmd_concat, check=True, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        print("[Error] Concatenation failed.")
        if os.path.exists(list_txt): os.remove(list_txt)
        return

    # 3. 音声合成 (映像copy / 音声aac / FPS維持)
    print(f"Adding Audio from original source...")
    cmd_mux = [
        "ffmpeg", "-y",
        "-i", temp_concat,          # 映像 (AI)
        "-i", original_video_path,  # 音声 (元動画)
        "-map", "0:v",              # 映像トラック
        "-map", "1:a?",             # 音声トラック(あれば)
        "-c:v", "copy",             # ★再エンコードなし（絶対ズレない）
        "-c:a", "aac",              # 音声はAAC
        "-shortest",                # 短い方に合わせる
        output_filename
    ]
    
    try:
        subprocess.run(cmd_mux, check=True, stderr=subprocess.DEVNULL)
        print(f"Success! Saved to: {output_filename}")
    except:
        print("[Error] Audio Muxing failed.")
    
    # お掃除
    if os.path.exists(list_txt): os.remove(list_txt)
    if os.path.exists(temp_concat): os.remove(temp_concat)

# ---------------------------------------------------------
# Worker Process
# ---------------------------------------------------------
def worker_process(video_path, workflow_file, start_frame, run_id):
    try:
        start_frame = int(start_frame)
        
        # FPS情報の取得
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened(): sys.exit(1)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS) # ★元動画のFPSを取得
        cap.release()

        chunk_index = start_frame // CHUNK_SIZE
        part_prefix = f"{run_id}_part_{chunk_index:03d}"
        
        # 既に終わってるかチェック
        search_pattern = os.path.join(COMFYUI_OUTPUT_DIR, f"{part_prefix}*{OUTPUT_EXT}")
        if glob.glob(search_pattern):
            sys.exit(0)

        current_cap = min(CHUNK_SIZE, total_frames - start_frame)
        print(f"[Worker] Chunk {chunk_index}: Processing {current_cap} frames (Target FPS: {fps:.4f})...")

        with open(workflow_file, "r", encoding="utf-8") as f:
            workflow = json.load(f)

        # ノード設定の更新
        if NODE_ID_LOADER in workflow:
            workflow[NODE_ID_LOADER]["inputs"]["frame_load_cap"] = current_cap
            workflow[NODE_ID_LOADER]["inputs"]["skip_first_frames"] = start_frame
            workflow[NODE_ID_LOADER]["inputs"]["video"] = os.path.abspath(video_path)
            # 念のため読み込み時もFPSを指定
            workflow[NODE_ID_LOADER]["inputs"]["force_rate"] = fps 

        if NODE_ID_SAVER in workflow:
            workflow[NODE_ID_SAVER]["inputs"]["filename_prefix"] = part_prefix
            # ★保存FPSを元動画と完全に一致させる
            workflow[NODE_ID_SAVER]["inputs"]["frame_rate"] = fps 

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
    print(f"=== Manager Started: Exact-FPS Mode ({MAX_PARALLEL_WORKERS} workers) ===")
    
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened(): return
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    cap.release()
    
    print(f"Original Video: {total_frames} frames / {fps:.3f} fps")
    print("★ Processing will maintain exact frame rate.")

    base_name = os.path.splitext(os.path.basename(video_path))[0]
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
                # 既存ファイルスキップ
                if glob.glob(os.path.join(COMFYUI_OUTPUT_DIR, f"{part_prefix}*{OUTPUT_EXT}")):
                    continue

                cmd = [sys.executable, __file__, video_path, workflow_file, 
                       "--worker_mode", "--start_frame", str(next_start_frame), "--run_id", run_id]
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
        print("❌ Worker Error. Stopping.")
        sys.exit(1)

    print("\n>>> All chunks completed!")

    # 結合処理
    all_files = glob.glob(os.path.join(COMFYUI_OUTPUT_DIR, f"{run_id}_part_*{OUTPUT_EXT}"))
    all_files.sort()
    
    if all_files:
        final_output_name = f"{run_id}_merged{OUTPUT_EXT}"
        merge_videos_exact_fps(all_files, os.path.join(COMFYUI_OUTPUT_DIR, final_output_name), video_path, fps)

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