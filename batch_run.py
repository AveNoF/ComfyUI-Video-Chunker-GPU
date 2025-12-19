import os
import glob
import subprocess
import time
import shutil
import sys

# ================= 設定エリア =================
INPUT_DIR = "./input_videos" 
TEMP_CFR_DIR = "./input_videos/temp_cfr_ready" # 変換済み動画の一時置き場
DONE_DIR = "./queue_done"
WORKFLOW_FILE = "workflow_api.json"
TARGET_FPS = 30  # ★変換するFPS (30 or 60)

# ComfyUIの出力フォルダ
USER_HOME = os.path.expanduser("~")
COMFYUI_OUTPUT_DIR = os.path.join(USER_HOME, "ComfyUI", "output")

EXTENSIONS = ["*.mp4", "*.avi", "*.mov", "*.mkv"]
# ============================================

def get_latest_merged_file(directory):
    search_pattern = os.path.join(directory, "*_merged.mp4")
    files = glob.glob(search_pattern)
    if not files: return None
    return max(files, key=os.path.getctime)

def convert_to_cfr(input_path, output_path):
    """動画を強制的に固定フレームレート(CFR)に変換する"""
    print(f"   ...Converting: {os.path.basename(input_path)}")
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-r", str(TARGET_FPS), 
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "aac", 
        output_path
    ]
    try:
        subprocess.run(cmd, check=True, stderr=subprocess.DEVNULL)
        return True
    except subprocess.CalledProcessError:
        print("   ❌ Conversion failed.")
        return False

def main():
    # フォルダ準備
    for d in [INPUT_DIR, TEMP_CFR_DIR, DONE_DIR]:
        if not os.path.exists(d):
            os.makedirs(d)

    # 1. 入力動画リスト取得
    raw_files = []
    for ext in EXTENSIONS:
        # tempフォルダの中身は拾わないように注意
        candidates = glob.glob(os.path.join(INPUT_DIR, ext))
        for f in candidates:
            if "temp_cfr_ready" not in f:
                raw_files.append(f)
    
    raw_files.sort()

    if not raw_files:
        print(f"⚠️ No video files found in '{INPUT_DIR}'.")
        return

    # ==========================================
    # Phase 1: 全ファイルをCFR変換 (下準備)
    # ==========================================
    print(f"\n🎬 === Phase 1: Converting {len(raw_files)} videos to {TARGET_FPS}fps CFR ===")
    
    converted_list = [] # (元ファイルのパス, CFRファイルのパス) のタプル

    for i, video_path in enumerate(raw_files):
        filename = os.path.basename(video_path)
        basename_no_ext = os.path.splitext(filename)[0]
        temp_cfr_path = os.path.join(TEMP_CFR_DIR, f"{basename_no_ext}_cfr.mp4")

        print(f"[{i+1}/{len(raw_files)}] {filename}")
        
        # 既に変換済みならスキップ（時短）
        if os.path.exists(temp_cfr_path):
            print("   ✅ Already converted. Skipping.")
        else:
            if not convert_to_cfr(video_path, temp_cfr_path):
                continue
        
        converted_list.append((video_path, temp_cfr_path))

    print("\n✨ All videos are converted to CFR format.")
    
    # ==========================================
    # Phase 2: ユーザー確認
    # ==========================================
    while True:
        choice = input("\n🚀 Proceed with AI Upscaling for all files? (y/n): ").lower()
        if choice in ['y', 'yes']:
            break
        elif choice in ['n', 'no']:
            print("❌ Cancelled. Converted files are kept in 'input_videos/temp_cfr_ready'.")
            sys.exit(0)

    # ==========================================
    # Phase 3: AI生成 & 結合 (本番)
    # ==========================================
    print(f"\n🤖 === Phase 3: Starting AI Processing for {len(converted_list)} videos ===")

    for i, (original_path, cfr_path) in enumerate(converted_list):
        filename = os.path.basename(original_path)
        basename_no_ext = os.path.splitext(filename)[0]
        
        print(f"\n🔥 Processing [{i+1}/{len(converted_list)}]: {filename}")

        # タイムスタンプ取得（生成後のファイル検知用）
        before_latest = get_latest_merged_file(COMFYUI_OUTPUT_DIR)
        before_time = os.path.getctime(before_latest) if before_latest else 0

        # process_video.py には「CFR化された動画」を渡す
        cmd = [sys.executable, "process_video.py", cfr_path, WORKFLOW_FILE]
        
        try:
            subprocess.run(cmd, check=True)
            print(f"   ✅ Generation Completed.")

            # --- 成果物のリネーム ---
            after_latest = get_latest_merged_file(COMFYUI_OUTPUT_DIR)
            
            if after_latest and os.path.getctime(after_latest) > before_time:
                new_name = f"{basename_no_ext}_upscaled.mp4"
                new_path = os.path.join(COMFYUI_OUTPUT_DIR, new_name)
                try:
                    if os.path.exists(new_path):
                        base, ext = os.path.splitext(new_name)
                        new_name = f"{base}_{int(time.time())}{ext}"
                        new_path = os.path.join(COMFYUI_OUTPUT_DIR, new_name)
                    os.rename(after_latest, new_path)
                    print(f"   ✨ Output saved to: ComfyUI/output/{new_name}")
                except OSError: pass
            else:
                print("   ⚠️ Warning: Output file not found.")

            # --- お片付け ---
            # 1. 元動画を queue_done へ
            shutil.move(original_path, os.path.join(DONE_DIR, filename))
            # 2. 一時CFRファイルを削除
            if os.path.exists(cfr_path):
                os.remove(cfr_path)
            
            print(f"   🚚 Finished & Moved to done.")

        except subprocess.CalledProcessError:
            print(f"   ❌ Error occurred. Skipping this file.")

    # 最後に一時フォルダが空なら消す
    if not os.listdir(TEMP_CFR_DIR):
        os.rmdir(TEMP_CFR_DIR)

    print("\n🎉 === All Jobs Finished Successfully! ===")

if __name__ == "__main__":
    main()