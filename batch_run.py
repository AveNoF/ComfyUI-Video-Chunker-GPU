import os
import glob
import subprocess
import time
import shutil

# ================= 設定エリア =================
# 変換したい動画が入っているフォルダ
INPUT_DIR = "./input_videos" 
# 完了した動画を移動させるフォルダ
DONE_DIR = "./queue_done"
# 使うワークフロー
WORKFLOW_FILE = "workflow_api.json"

# ComfyUIの出力フォルダ（成果物を探すため）
# ※ Windows/Linux自動判定
USER_HOME = os.path.expanduser("~")
COMFYUI_OUTPUT_DIR = os.path.join(USER_HOME, "ComfyUI", "output")

EXTENSIONS = ["*.mp4", "*.avi", "*.mov", "*.mkv"]
# ============================================

def get_latest_merged_file(directory):
    # *_merged.mp4 の中で一番新しいファイルを探す
    search_pattern = os.path.join(directory, "*_merged.mp4")
    files = glob.glob(search_pattern)
    if not files:
        return None
    # 作成日時順にソートして最後（最新）を返す
    return max(files, key=os.path.getctime)

def main():
    if not os.path.exists(INPUT_DIR):
        os.makedirs(INPUT_DIR)
        print(f"フォルダ '{INPUT_DIR}' を作成しました。")
        return

    if not os.path.exists(DONE_DIR):
        os.makedirs(DONE_DIR)

    video_files = []
    for ext in EXTENSIONS:
        video_files.extend(glob.glob(os.path.join(INPUT_DIR, ext)))
    
    video_files.sort()

    if not video_files:
        print(f"'{INPUT_DIR}' に動画ファイルがありません。")
        return

    print(f"=== 全 {len(video_files)} 個の動画を処理します ===")

    for i, video_path in enumerate(video_files):
        filename = os.path.basename(video_path)
        basename_no_ext = os.path.splitext(filename)[0]
        
        print(f"\n[{i+1}/{len(video_files)}] Processing: {filename}")
        
        # 処理前の最新ファイルを記録（これより新しいのができたら成果物とみなす）
        before_latest = get_latest_merged_file(COMFYUI_OUTPUT_DIR)
        before_time = os.path.getctime(before_latest) if before_latest else 0

        cmd = ["python", "process_video.py", video_path, WORKFLOW_FILE]
        
        try:
            subprocess.run(cmd, check=True)
            print(f"✅ Processing Done: {filename}")

            # === 成果物のリネーム処理 ===
            after_latest = get_latest_merged_file(COMFYUI_OUTPUT_DIR)
            
            if after_latest and os.path.getctime(after_latest) > before_time:
                # 新しいファイルができている！ -> リネーム
                new_name = f"{basename_no_ext}_upscaled.mp4"
                new_path = os.path.join(COMFYUI_OUTPUT_DIR, new_name)
                
                # 既に同名ファイルがある場合は上書きなどの対策（ここでは単純リネーム）
                try:
                    os.rename(after_latest, new_path)
                    print(f"✨ Renamed output to: {new_name}")
                except OSError as e:
                    print(f"⚠️ Rename failed: {e}")
            else:
                print("⚠️ Warning: Output file not found (or timestamp didn't update).")

            # === 元動画の移動 ===
            dest_path = os.path.join(DONE_DIR, filename)
            shutil.move(video_path, dest_path)
            print(f"🚚 Input moved to: {DONE_DIR}")

        except subprocess.CalledProcessError:
            print(f"❌ Error: {filename}")
            time.sleep(5) 
        except Exception as e:
            print(f"❌ Unexpected Error: {e}")

    print("\n=== 全て完了しました ===")

if __name__ == "__main__":
    main()
