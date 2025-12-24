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
CHUNK_SIZE = 500           # メモリ不足対策で500
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

def merge_videos_in_folder_smart(target_folder, output_filename, original_video_path):
    """
    重複排除・オーディオ有無の選別を行い、スマートに結合する。
    """
    print(f"\n=== Merging files inside: {os.path.basename(target_folder)} ===")
    
    # 全てのmp4を取得
    search_pattern = os.path.join(target_folder, f"*{OUTPUT_EXT}")
    all_files = glob.glob(search_pattern)
    
    if not all_files:
        print("❌ No part files found in the folder.")
        return

    # パート番号ごとにファイルをグループ化
    # key: パート番号(int), value: [ファイルパスのリスト]
    part_map = {}
    pattern = re.compile(r"part_(\d+)")

    for f_path in all_files:
        fname = os.path.basename(f_path)
        # "merged" という名前が入っているファイル（以前の結合結果など）は除外
        if "merged" in fname: continue
        
        match = pattern.search(fname)
        if match:
            part_idx = int(match.group(1))
            if part_idx not in part_map: part_map[part_idx] = []
            part_map[part_idx].append(f_path)
    
    # 結合リストを作成
    final_list = []
    sorted_indices = sorted(part_map.keys())
    
    print(f"   Found {len(sorted_indices)} unique parts.")

    for idx in sorted_indices:
        candidates = part_map[idx]
        
        # 候補が複数ある場合（例: part_001.mp4 と part_001-audio.mp4）
        selected_file = candidates[0]
        
        if len(candidates) > 1:
            # 基本的にファイル名が短い方（余計なサフィックスがない方）を選ぶ、
            # またはファイルサイズが大きい方を選ぶなどのロジックを入れる
            # ここでは「-audio」などがついていないシンプルなものを優先するロジック
            candidates.sort(key=len) 
            selected_file = candidates[0] 
            # もしaudio付きを優先したいなら逆にするが、結合時に元動画の音声を使うので映像のみでOK
            
        final_list.append(selected_file)

    # リスト書き出し
    list_txt = os.path.join(target_folder, "concat_list.txt")
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
        
        # フォルダ指定
        part_prefix = f"{run_dir_name}/part_{chunk_index:03d}"
        full_output_dir = os.path.join(COMFYUI_OUTPUT_DIR, run_dir_name)
        
        # 重複生成チェック
        # part_001.mp4, part_001_00001_.mp4 などバリエーションがあっても「1つあればOK」とする
        search_pattern = os.path.join(full_output_dir, f"part_{chunk_index:03d}*{OUTPUT_EXT}")
        existing = glob.glob(search_pattern)

        if existing:
            # 0バイトでなければスキップ
            if any(os.path.getsize(f) > 1024 for f in existing):
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
    print(f"=== Manager Started (Smart Isolated Mode) ===")
    cap = cv2.VideoCapture(original_video_path)
    if not cap.isOpened(): return
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    
    base_name = os.path.splitext(os.path.basename(original_video_path))[0]
    safe_base_name = "".join([c if c.isalnum() or c in (' ', '.', '_', '-') else '_' for c in base_name])[:30]
    
    # フォルダ再利用ロジック
    potential_dirs = glob.glob(os.path.join(COMFYUI_OUTPUT_DIR, f"{safe_base_name}_*"))
    potential_dirs = [d for d in potential_dirs if os.path.isdir(d)]
    
    if potential_dirs:
        target_dir_path = max(potential_dirs, key=os.path.getmtime)
        run_dir_name = os.path.basename(target_dir_path)
        print(f"🔄 Resuming job in existing folder: {run_dir_name}")
    else:
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
                
                # ワーカー起動前チェック（高速化）
                search_pattern = os.path.join(target_dir_path, f"part_{chunk_index:03d}*{OUTPUT_EXT}")
                if glob.glob(search_pattern):
                    # print(f"[Manager] Chunk {chunk_index} exists. Skipping spawn.") 
                    # ログがうるさいのでコメントアウトしてもOK
                    continue

                cmd = [sys.executable, __file__, original_video_path, workflow_file, 
                       "--worker_mode", 
                       "--start_frame", str(next_start_frame), 
                       "--run_id", run_dir_name]
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
        final_output_name = os.path.join(COMFYUI_OUTPUT_DIR, f"{run_dir_name}_merged{OUTPUT_EXT}")
        # スマート結合を実行
        merge_videos_in_folder_smart(target_dir_path, final_output_name, original_video_path)

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