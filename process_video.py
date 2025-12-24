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
import shutil

# ================= Configuration =================
COMFYUI_URL = "http://127.0.0.1:8188"
DEFAULT_WORKFLOW_FILE = "workflow_api.json"
CHUNK_SIZE = 500           # メモリ不足対策で500に設定
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

def merge_videos_in_folder(target_folder, output_filename, original_video_path):
    """
    指定されたフォルダの中にある part_xxx.mp4 だけを結合する。
    他のフォルダのファイルが混ざることはない。
    """
    print(f"\n=== Merging files inside: {os.path.basename(target_folder)} ===")
    
    # このフォルダ内の part_xxx.mp4 を検索
    search_pattern = os.path.join(target_folder, f"part_*{OUTPUT_EXT}")
    files = glob.glob(search_pattern)
    
    if not files:
        print("❌ No part files found in the folder.")
        return

    # パート番号順に並べ替え
    def get_part_num(fname):
        match = re.search(r"part_(\d+)", fname)
        return int(match.group(1)) if match else 999999
    
    files.sort(key=get_part_num)

    list_txt = os.path.join(target_folder, "concat_list.txt")
    with open(list_txt, "w", encoding="utf-8") as f:
        for vid in files:
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
        # NVENCチェック
        subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        cmd_final[cmd_final.index("-c:v") + 1] = "h264_nvenc"
    except: pass 

    try:
        subprocess.run(cmd_final, check=True, stderr=subprocess.DEVNULL)
        print(f"✅ Merge Success! Saved to: {output_filename}")
    except:
        print("❌ Merge failed.")

    if os.path.exists(list_txt): os.remove(list_txt)

def worker_process(video_path, workflow_file, start_frame, run_dir_name):
    try:
        start_frame = int(start_frame)
        cap = cv2.VideoCapture(video_path)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        target_fps = 30.0 
        cap.release()

        chunk_index = start_frame // CHUNK_SIZE
        
        # フォルダ構成: ComfyUI/output/{run_dir_name}/part_001.mp4
        # ComfyUIのfilename_prefixに「フォルダ名/ファイル名」を指定すると自動でフォルダが掘られる
        part_prefix = f"{run_dir_name}/part_{chunk_index:03d}"
        
        # 生成済みチェック
        full_output_dir = os.path.join(COMFYUI_OUTPUT_DIR, run_dir_name)
        expected_filename = f"part_{chunk_index:03d}{OUTPUT_EXT}" # ComfyUIが保存する実際の名前
        # ※ComfyUIは番号を勝手につける場合があるが、prefixで指定すれば固定できることが多い
        
        # 簡易チェック: そのフォルダに part_001_xxxxx.mp4 みたいなのがあればスキップ
        search_pattern = os.path.join(full_output_dir, f"part_{chunk_index:03d}*{OUTPUT_EXT}")
        existing = glob.glob(search_pattern)

        if existing:
            if os.path.getsize(existing[0]) > 1024:
                print(f"[Worker] Chunk {chunk_index}: ✅ Exists in folder. Skipping.")
                sys.exit(0)
            else:
                 print(f"[Worker] Chunk {chunk_index}: ⚠️ Found empty file, regenerating.")

        current_cap = min(CHUNK_SIZE, total_frames - start_frame)
        print(f"[Worker] Chunk {chunk_index}: Generating {current_cap} frames into folder '{run_dir_name}'...")

        with open(workflow_file, "r", encoding="utf-8") as f:
            workflow = json.load(f)

        if NODE_ID_LOADER in workflow:
            workflow[NODE_ID_LOADER]["inputs"]["frame_load_cap"] = current_cap
            workflow[NODE_ID_LOADER]["inputs"]["skip_first_frames"] = start_frame
            workflow[NODE_ID_LOADER]["inputs"]["video"] = os.path.abspath(video_path)

        if NODE_ID_SAVER in workflow:
            # ★重要: ここでサブフォルダを指定
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
    print(f"=== Manager Started (Folder Isolation Mode) ===")
    cap = cv2.VideoCapture(original_video_path)
    if not cap.isOpened(): return
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    
    base_name = os.path.splitext(os.path.basename(original_video_path))[0]
    # ファイル名から安全なフォルダ名を作成
    safe_base_name = "".join([c if c.isalnum() or c in (' ', '.', '_', '-') else '_' for c in base_name])[:30]
    
    # === フォルダ戦略 ===
    # 1. outputフォルダの中に、この動画専用のフォルダがあるか探す (Resume機能)
    #    名前のパターン: "SafeName_Timestamp"
    potential_dirs = glob.glob(os.path.join(COMFYUI_OUTPUT_DIR, f"{safe_base_name}_*"))
    potential_dirs = [d for d in potential_dirs if os.path.isdir(d)]
    
    if potential_dirs:
        # 一番新しいフォルダを再利用する（続きから）
        target_dir_path = max(potential_dirs, key=os.path.getmtime)
        run_dir_name = os.path.basename(target_dir_path)
        print(f"🔄 Resuming job in existing folder: {run_dir_name}")
    else:
        # 新しいフォルダを作る
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir_name = f"{safe_base_name}_{timestamp}"
        target_dir_path = os.path.join(COMFYUI_OUTPUT_DIR, run_dir_name)
        os.makedirs(target_dir_path, exist_ok=True)
        print(f"🆕 Created new isolated folder: {run_dir_name}")

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
                
                # ワーカー起動前にチェック
                search_pattern = os.path.join(target_dir_path, f"part_{chunk_index:03d}*{OUTPUT_EXT}")
                if glob.glob(search_pattern):
                    print(f"[Manager] Chunk {chunk_index} exists. Skipping spawn.")
                    continue

                cmd = [sys.executable, __file__, original_video_path, workflow_file, 
                       "--worker_mode", 
                       "--start_frame", str(next_start_frame), 
                       "--run_id", run_dir_name] # ここでフォルダ名を渡す
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
        
        # 最終出力ファイル名（親フォルダに保存）
        final_output_name = os.path.join(COMFYUI_OUTPUT_DIR, f"{run_dir_name}_merged{OUTPUT_EXT}")
        
        # その専用フォルダの中身だけを使って結合
        merge_videos_in_folder(target_dir_path, final_output_name, original_video_path)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("video_path", nargs="?", help="Path to video file")
    parser.add_argument("workflow_file", nargs="?", default=DEFAULT_WORKFLOW_FILE, help="Path to workflow json")
    parser.add_argument("--worker_mode", action="store_true")
    parser.add_argument("--start_frame")
    parser.add_argument("--run_id") # workerモードではこれが「フォルダ名」になる
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