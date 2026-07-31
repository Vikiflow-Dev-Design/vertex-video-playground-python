import os
import subprocess
from pathlib import Path

# Paths
BASE_DIR = Path("C:/Users/victor/Desktop/google-cloud-video-automation/vertex-video-playground/vertex-video-playground")
PROJECT_DIR = BASE_DIR / "video_projects/the-entire-history-of-rome"

VEO_DIR = PROJECT_DIR / "veo"
TRIMMED_CUT_DIR = VEO_DIR / "trimmed/cut"
AUDIO_CLIPS_DIR = PROJECT_DIR / "audio/clips"
EXPORTS_DIR = PROJECT_DIR / "exports"

def main():
    print("========================================")
    print("STITCHING SECTION 1 MASTER VIDEO")
    print("========================================")
    
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Verification
    print("Verifying clips...")
    missing_videos = []
    missing_audios = []
    
    for i in range(1, 24):
        v_path = TRIMMED_CUT_DIR / f"clip_{i:03d}.mp4"
        a_path = AUDIO_CLIPS_DIR / f"clip_{i:03d}.mp3"
        
        if not v_path.exists():
            missing_videos.append(f"clip_{i:03d}.mp4")
        if not a_path.exists():
            missing_audios.append(f"clip_{i:03d}.mp3")

    if missing_videos:
        print(f"[ERROR] Missing {len(missing_videos)} video clips: {missing_videos}")
        return
    if missing_audios:
        print(f"[ERROR] Missing {len(missing_audios)} audio clips: {missing_audios}")
        return

    print("[OK] All 23 video and audio clips verified successfully.")

    # 2. Concatenate Audio Clips
    print("\nConcatenating audio narration clips...")
    concat_audio_txt = AUDIO_CLIPS_DIR / "concat_audio.txt"
    with open(concat_audio_txt, "w", encoding="utf-8") as f:
        for i in range(1, 24):
            # Write relative/escaped paths for ffmpeg concat
            path_str = f"clip_{i:03d}.mp3"
            f.write(f"file '{path_str}'\n")

    audio_output = EXPORTS_DIR / "section_1_narration.mp3"
    audio_cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_audio_txt),
        "-c", "copy",
        str(audio_output)
    ]
    res_audio = subprocess.run(audio_cmd, capture_output=True, text=True)
    if res_audio.returncode != 0:
        print(f"[ERROR] Audio concatenation failed: {res_audio.stderr}")
        return
    print(f"[OK] Section 1 narration merged: {audio_output}")

    # 3. Concatenate Video Clips
    print("\nConcatenating video clips...")
    concat_video_txt = TRIMMED_CUT_DIR / "concat_video.txt"
    with open(concat_video_txt, "w", encoding="utf-8") as f:
        for i in range(1, 24):
            path_str = f"clip_{i:03d}.mp4"
            f.write(f"file '{path_str}'\n")

    video_only_output = EXPORTS_DIR / "section_1_video_only.mp4"
    video_cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_video_txt),
        "-c", "copy",
        str(video_only_output)
    ]
    res_video = subprocess.run(video_cmd, capture_output=True, text=True)
    if res_video.returncode != 0:
        print(f"[ERROR] Video concatenation failed: {res_video.stderr}")
        return
    print(f"[OK] Section 1 video only merged: {video_only_output}")

    # 4. Mux Video and Audio
    print("\nMuxing video and audio tracks...")
    final_output = EXPORTS_DIR / "section_1_master.mp4"
    mux_cmd = [
        "ffmpeg", "-y",
        "-i", str(video_only_output),
        "-i", str(audio_output),
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        str(final_output)
    ]
    res_mux = subprocess.run(mux_cmd, capture_output=True, text=True)
    if res_mux.returncode != 0:
        print(f"[ERROR] Muxing failed: {res_mux.stderr}")
        return

    print("\n========================================")
    print("STITCHING SUCCESSFUL!")
    print("========================================")
    size_mb = final_output.stat().st_size / (1024 * 1024)
    print(f"Master Video Path: {final_output}")
    print(f"File Size:         {size_mb:.2f} MB")
    print("========================================\n")

    # Clean up intermediate video_only and narration files if desired
    try:
        video_only_output.unlink(missing_ok=True)
        audio_output.unlink(missing_ok=True)
        concat_audio_txt.unlink(missing_ok=True)
        concat_video_txt.unlink(missing_ok=True)
    except Exception as e:
        print(f"[Warning] Failed to clean up temp files: {e}")

if __name__ == "__main__":
    main()
