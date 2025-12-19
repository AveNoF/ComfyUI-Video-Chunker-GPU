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
import shutil

# ================= Configuration =================
COMFYUI_URL = "http://127.0.0.1:8188"
DEFAULT_WORKFLOW_FILE = "workflow_api.json"
CHUNK_SIZE = 1000          
MAX_PARALLEL_WORKERS = 1   
OUTPUT_EXT = ".mp4"
NODE_ID_LOADER = "1"       # Load Video
NODE_ID_SAVER = "4"        # Video Combine (IDが4であることを確認済み)
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

# ★新機能: VFR(可変フレームレート)をCFR(固定)に強制変換する
def convert_to_cfr(input_path):
    print("\n🔍 Checking video format...")
    
    # 1. 元のFPSを探る
    cap = cv2.VideoCapture(input_path)
    fps = cap.get(cv2.CAP_PROP_FPS)
    cap.release()
    
    if fps <= 0 or fps > 120: fps = 30.0 # 異常値なら30に固定
    
    print(f"   Detected Base FPS: {fps}")
    print("⚡ Converting VFR to CFR (Fixed Frame Rate) to prevent audio desync...")
    
    # 作業用ファイル名
    base, _ = os.path.splitext(input_path)
    output_cfr = base + "_CFR_TEMP.mp4"
    
    # ffmpegで強制的に固定FPSに書き直す (-r 指定 + -vsync cfr)
    # 画質は作業用なのでそこそこで高速に (ultrafast, crf 18)
    cmd = [
        "ffmpeg", "-y", 
        "-i", input_path,
        "-r", str(fps),          # FPSを強制固定
        "-vsync", "cfr",         # VFRを無効化
        "-c:v", "libx264", 
        "-preset", "ultrafast", 
        "-crf", "18",
        "-c:a", "copy",          # 音声はコピー
        output_cfr
    ]
    
    try:
        subprocess.run(cmd, check=True, stderr=subprocess.DEVNULL)
        print(f"   ✅ Conversion Complete: {os.path.basename(output_cfr)}")
        return output_cfr, fps
    except:
        print("   ❌ Conversion failed. Using original file (Sync might be off).")
        return input_path, fps

# 結合処理
def merge_videos_exact_fps(file_list, output_filename, audio_source_path, fps):
    print(f"\n=== Merging {len(file_list)} parts (FPS: {fps}) ===")
    
    temp_concat = output_filename.replace(".mp4", "_temp.mp4")
    list_txt = "concat_list.txt"
    
    with open(list_txt, "w", encoding="utf-8") as f:
        for vid in file_list:
            safe_vid = vid.replace("'", "'\\''")
            f.write(f"file '{safe_vid}'\n")

    # 結合
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_txt, 
        "-c", "copy", temp_concat
    ], stderr=subprocess.DEVNULL)

    # 音声合成 (変換後のCFR動画から音をもらうことでズレを防止)
    print(f"Adding Audio...")
    cmd_mux = [
        "ffmpeg", "-y",
        "-i", temp_concat,          
        "-i", audio_source_path,    
        "-map", "0:v",              
        "-map", "1:a?",             
        "-c:v", "copy",             
        "-c:a", "aac",              
        "-shortest",                
        output_filename
    ]
    
    try:
        subprocess.run(cmd_mux, check=True, stderr=subprocess.DEVNULL)
        print(f"Success! Saved to: {output_filename}")
    except:
        print("[Error] Audio Muxing failed.")
    
    if os.path.exists(list_txt): os.remove(list_txt)
    if os.path.exists(temp_concat): os.remove(temp_concat)

# ---------------------------------------------------------
# Worker
# ---------------------------------------------------------
def worker_process(video_path, workflow_file, start_frame, run_id, fixed_fps):
    try:
        start_frame = int(start_frame)
        fps = float(fixed_fps)

        # 動画長さ確認
        cap = cv2.VideoCapture(video_path)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        cap.release()

        chunk_index = start_frame // CHUNK_SIZE
        part_prefix = f"{run_id}_part_{chunk_index:03d}"
        
        search_pattern = os.path.join(COMFYUI_OUTPUT_DIR, f"{part_prefix}*{OUTPUT_EXT}")
        if glob.glob(search_pattern):
            sys.exit(0)

        current_cap = min(CHUNK_SIZE, total_frames - start_frame)
        print(f"[Worker] Chunk {chunk_index}: {current_cap} frames (FPS: {fps})...")

        with open(workflow_file, "r", encoding="utf-8") as f:
            workflow = json.load(f)

        if NODE_ID_LOADER in workflow:
            workflow[NODE_ID_LOADER]["inputs"]["frame_load_cap"] = current_cap
            workflow[NODE_ID_LOADER]["inputs"]["skip_first_frames"] = start_frame
            workflow[NODE_ID_LOADER]["inputs"]["video"] = os.path.abspath(video_path)
            # CFR化したのでForce Rateは不要だが念のため
            # workflow[NODE_ID_LOADER]["inputs"]["force_rate"] = fps 

        if NODE_ID_SAVER in workflow:
            workflow[NODE_ID_SAVER]["inputs"]["filename_prefix"] = part_prefix
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
# Manager
# ---------------------------------------------------------
def manager_process(original_video_path, workflow_file):
    print(f"=== Manager Started: VFR-Safe Mode ({MAX_PARALLEL_WORKERS} workers) ===")
    
    # ★ここで最初にCFR変換を実行！
    # 処理にはこの「整えられた一時ファイル」を使う
    working_video, fps = convert_to_cfr(original_video_path)
    
    cap = cv2.VideoCapture(working_video)
    if not cap.isOpened(): return
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    
    print(f"Target Video: {total_frames} frames / {fps:.3f} fps")

    # ID生成
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

                # ワーカーには「CFR変換後のファイル」を渡す
                cmd = [sys.executable, __file__, working_video, workflow_file, 
                       "--worker_mode", 
                       "--start_frame", str(next_start_frame), 
                       "--run_id", run_id,
                       "--fps", str(fps)]
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

    # 結合処理
    if not error_occurred:
        print("\n>>> All chunks completed!")
        all_files = glob.glob(os.path.join(COMFYUI_OUTPUT_DIR, f"{run_id}_part_*{OUTPUT_EXT}"))
        all_files.sort()
        
        if all_files:
            final_output_name = f"{run_id}_merged{OUTPUT_EXT}"
            # 音声も「CFR変換後のファイル」から取ることでタイミングを合わせる
            merge_videos_exact_fps(all_files, os.path.join(COMFYUI_OUTPUT_DIR, final_output_name), working_video, fps)

    # 一時ファイルの掃除
    if working_video != original_video_path and os.path.exists(working_video):
        print(f"Cleaning up temp file: {working_video}")
        os.remove(working_video)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("video_path", nargs="?", help="Path to video file")
    parser.add_argument("workflow_file", nargs="?", default=DEFAULT_WORKFLOW_FILE, help="Path to workflow json")
    parser.add_argument("--worker_mode", action="store_true")
    parser.add_argument("--start_frame")
    parser.add_argument("--run_id")
    parser.add_argument("--fps")
    args = parser.parse_args()

    if not args.video_path:
        if args.worker_mode: sys.exit(1)
        try:
            input_path = input("Enter video file path: ").strip().strip("'").strip('"')
            if not input_path: sys.exit(0)
            args.video_path = input_path
        except: sys.exit(0)

    if args.worker_mode:
        worker_process(args.video_path, args.workflow_file, args.start_frame, args.run_id, args.fps)
    else:
        manager_process(args.video_path, args.workflow_file)