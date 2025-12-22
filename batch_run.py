import os
import glob
import subprocess
import time
import shutil
import sys

# 文字化け対策
sys.stdout.reconfigure(encoding='utf-8')

# ================= 設定エリア =================
INPUT_DIR = "./input_videos" 
TEMP_CFR_DIR = "./input_videos/temp_cfr_ready" 
DONE_DIR = "./queue_done"
WORKFLOW_FILE = "workflow_api.json"
TARGET_FPS = 30 

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
    
    # 絶対パスに変換（FFmpegのパス解決ミスを防ぐ）
    abs_input = os.path.abspath(input_path)
    abs_output = os.path.abspath(output_path)

    cmd = [
        "ffmpeg", "-y", "-i", abs_input,
        "-r", str(TARGET_FPS), 
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "aac", 
        abs_output
    ]
    try:
        # エラーが見えるように capture_output=True に変更
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"   ❌ Conversion failed.")
        # エラー内容の一部を表示（最後の2行など）
        print(f"   [Error Log]: {e.stderr[-300:]}") 
        return False

def main():
    for d in [INPUT_DIR, TEMP_CFR_DIR, DONE_DIR]:
        if not os.path.exists(d):
            os.makedirs(d)

    raw_files = []
    for ext in EXTENSIONS:
        candidates = glob.glob(os.path.join(INPUT_DIR, ext))
        for f in candidates:
            if "temp_cfr_ready" not in f:
                raw_files.append(f)
    
    raw_files.sort()

    if not raw_files:
        print(f"⚠️ No video files found in '{INPUT_DIR}'.")
        return

    # ==========================================
    # Phase 1: CFR変換
    # ==========================================
    print(f"\n🎬 === Phase 1: Converting {len(raw_files)} videos to {TARGET_FPS}fps CFR ===")
    
    converted_list = [] 

    for i, video_path in enumerate(raw_files):
        filename = os.path.basename(video_path)
        # 拡張子を除いたファイル名を取得
        basename_no_ext = os.path.splitext(filename)[0]
        
        # 一時ファイル名に特殊文字が含まれないようにハッシュ化などを検討すべきだが、
        # まずは絶対パスで解決を図る
        temp_cfr_path = os.path.join(TEMP_CFR_DIR, f"{basename_no_ext}_cfr.mp4")

        print(f"[{i+1}/{len(raw_files)}] {filename}")
        
        if os.path.exists(temp_cfr_path):
            # サイズが0バイトなら破損しているので再作成
            if os.path.getsize(temp_cfr_path) > 1024:
                print("   ✅ Already converted. Skipping.")
                converted_list.append((video_path, temp_cfr_path))
                continue
            else:
                os.remove(temp_cfr_path)

        if convert_to_cfr(video_path, temp_cfr_path):
            converted_list.append((video_path, temp_cfr_path))
        else:
            print(f"   ⚠️ Skipping {filename} due to conversion error.")

    if not converted_list:
        print("\n❌ No videos were successfully converted. Check filenames or FFmpeg.")
        return

    print(f"\n✨ {len(converted_list)} videos are ready for AI processing.")
    
    # ==========================================
    # Phase 2: 確認
    # ==========================================
    while True:
        choice = input("\n🚀 Proceed with AI Upscaling for listed files? (y/n): ").lower()
        if choice in ['y', 'yes']:
            break
        elif choice in ['n', 'no']:
            print("❌ Cancelled.")
            sys.exit(0)

    # ==========================================
    # Phase 3: AI生成
    # ==========================================
    print(f"\n🤖 === Phase 3: Starting AI Processing for {len(converted_list)} videos ===")

    for i, (original_path, cfr_path) in enumerate(converted_list):
        filename = os.path.basename(original_path)
        basename_no_ext = os.path.splitext(filename)[0]
        
        print(f"\n🔥 Processing [{i+1}/{len(converted_list)}]: {filename}")

        before_latest = get_latest_merged_file(COMFYUI_OUTPUT_DIR)
        before_time = os.path.getctime(before_latest) if before_latest else 0

        cmd = [sys.executable, "process_video.py", cfr_path, WORKFLOW_FILE]
        
        try:
            subprocess.run(cmd, check=True)
            print(f"   ✅ Generation Completed.")

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

            # 移動と掃除
            shutil.move(original_path, os.path.join(DONE_DIR, filename))
            if os.path.exists(cfr_path):
                os.remove(cfr_path)
            
            print(f"   🚚 Finished & Moved to done.")

        except subprocess.CalledProcessError:
            print(f"   ❌ Error occurred during AI processing.")

    if not os.listdir(TEMP_CFR_DIR):
        try: os.rmdir(TEMP_CFR_DIR)
        except: pass

    print("\n🎉 === All Jobs Finished Successfully! ===")

if __name__ == "__main__":
    main()