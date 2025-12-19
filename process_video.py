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

# VFR対策：時間を強制的に合わせる結合
def merge_videos_force_sync(file_list, output_filename, original_video_path):
    print(f"\n=== Merging {len(file_list)} parts with ABSOLUTE DURATION SYNC ===")
    
    # 1. まず単純結合（映像のみ）
    temp_concat = output_filename.replace(".mp4", "_temp_concat.mp4")
    list_txt = "concat_list.txt"
    
    with open(list_txt, "w", encoding="utf-8") as f:
        for vid in file_list:
            safe_vid = vid.replace("'", "'\\''")
            f.write(f"file '{safe_vid}'\n")

    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_txt, 
        "-c", "copy", temp_concat
    ], stderr=subprocess.DEVNULL)

    # 2. 時間のズレを計算
    duration_orig = get_exact_duration(original_video_path)
    duration_ai = get_exact_duration(temp_concat)
    
    scale_factor = 1.0
    if duration_orig > 0 and duration_ai > 0:
        scale_factor = duration_orig / duration_ai
        print(f"   Original Duration: {duration_orig:.4f}s")
        print(f"   AI Video Duration: {duration_ai:.4f}s")
        print(f"   ⚡ Sync Correction: Stretching video by {scale_factor:.6f}x")
    else:
        print("   ⚠️ Duration check failed. Assuming 1.0x.")

    # 3. 強制同期合成
    # setptsで映像の長さを変える + 音声は元動画からそのまま使う + 最長時間を確保
    cmd_final = [
        "ffmpeg", "-y",
        "-i", temp_concat,          # [0] AI映像
        "-i", original_video_path,  # [1] 元動画(音声のマスター)
        "-filter_complex", f"[0:v]setpts=PTS*{scale_factor}[v]", 
        "-map", "[v]",              # 伸縮した映像
        "-map", "1:a?",             # 元の音声(絶対)
        "-c:v", "libx264",          # 再エンコード
        "-preset", "p5",            
        "-crf", "18",               
        "-c:a", "aac",              # 音声変換
        # -shortest は外す（音声が切れるのを防ぐ）
        # -t で「元動画の秒数」で強制的に切ることも可能だが、
        # 映像をストレッチしているので自然に合うはず
        output_filename
    ]
    
    # GPUエンコードチェック
    try:
        subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        cmd_final[cmd_final.index("-c:v") + 1] = "h264_nvenc"
    except:
        pass 

    try:
        subprocess.run(cmd_final, check=True, stderr=subprocess.DEVNULL)
        print(f"✅ Success! Video duration is forced to match original: {output_filename}")
    except:
        print("❌ Final Merge failed. Trying fallback copy...")
        subprocess.run([
            "ffmpeg", "-y", "-i", temp_concat, "-i", original_video_path,
            "-map", "0:v", "-map", "1:a?", "-c", "copy", output_filename
        ], stderr=subprocess.DEVNULL)

    if os.path.exists(list_txt): os.remove(list_txt)
    if os.path.exists(temp_concat): os.remove(temp_concat)

def worker_process(video_path, workflow_file, start_frame, run_id):
    try:
        start_frame = int(start_frame)

        cap = cv2.VideoCapture(video_path)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        # VFR対策: FPSは決め打ちで60にして、とにかく「全フレーム」を出させる
        target_fps = 60.0 
        cap.release()

        chunk_index = start_frame // CHUNK_SIZE
        part_prefix = f"{run_id}_part_{chunk_index:03d}"
        
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
            # force_rateは指定しない（全フレーム抽出優先）

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
    print(f"=== Manager Started: Absolute-Duration Sync Mode ===")
    
    # フレームカウント取得
    cap = cv2.VideoCapture(original_video_path)
    if not cap.isOpened(): return
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    
    print(f"Processing Target: {total_frames} frames")

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
        all_files = glob.glob(os.path.join(COMFYUI_OUTPUT_DIR, f"{run_id}_part_*{OUTPUT_EXT}"))
        all_files.sort()
        
        if all_files:
            final_output_name = f"{run_id}_merged{OUTPUT_EXT}"
            merge_videos_force_sync(all_files, os.path.join(COMFYUI_OUTPUT_DIR, final_output_name), original_video_path)

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