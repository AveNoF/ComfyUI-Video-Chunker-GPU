import os
import glob
import subprocess
import time

# ================= 設定エリア =================
# 変換したい動画が入っているフォルダ
INPUT_DIR = "./input_videos" 

# 使うワークフローファイル
WORKFLOW_FILE = "workflow_api.json"

# 対応する拡張子
EXTENSIONS = ["*.mp4", "*.avi", "*.mov", "*.mkv"]
# ============================================

def main():
    # 入力フォルダがなければ作る
    if not os.path.exists(INPUT_DIR):
        os.makedirs(INPUT_DIR)
        print(f"フォルダ '{INPUT_DIR}' を作成しました。ここに動画を入れてください。")
        return

    # ファイルリストを取得
    video_files = []
    for ext in EXTENSIONS:
        video_files.extend(glob.glob(os.path.join(INPUT_DIR, ext)))
    
    video_files.sort()

    if not video_files:
        print(f"'{INPUT_DIR}' フォルダに動画ファイルが見つかりません。")
        return

    print(f"=== 全 {len(video_files)} 個の動画を連続処理します ===")

    for i, video_path in enumerate(video_files):
        print(f"\n[{i+1}/{len(video_files)}] Processing: {os.path.basename(video_path)}")
        
        # process_video.py を呼び出す
        # 完了するまで待機してから次の動画へ進む
        cmd = ["python", "process_video.py", video_path, WORKFLOW_FILE]
        
        try:
            subprocess.run(cmd, check=True)
            print(f"✅ Finished: {os.path.basename(video_path)}")
        except subprocess.CalledProcessError:
            print(f"❌ Error occurred: {os.path.basename(video_path)}")
            # エラーでも止まらず次へ行くなら continue、止めるなら break
            time.sleep(5) 

    print("\n=== 全てのバッチ処理が完了しました ===")

if __name__ == "__main__":
    main()