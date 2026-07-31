#!/usr/bin/env python3
"""Generate a video with Vertex AI / Veo and optionally persist metadata to MongoDB.

Examples:

  python generate_video.py \
    --project-id your-project-id \
    --prompt "a cinematic drone shot over a neon city at night" \
    --output-gcs-uri gs://your-bucket/videos/

  python generate_video.py \
    --project-dir video_projects/carthage \
    --prompt "a vertical fashion ad with dramatic lighting"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from google import genai
from google.genai.types import GenerateVideosConfig

from mongo_store import build_mediaasset_doc, connect, resolve_settings, utc_now, upsert_mediaasset

ROOT = Path(__file__).resolve().parent
WORKSPACE_DEFAULTS_PATH = ROOT / "video_projects" / "_workspace_defaults.json"
DEFAULT_VEO_MODEL = "veo-3.1-lite-generate-001"
DEFAULT_VEO_RESOLUTION = "720p"
DEFAULT_VEO_GENERATE_AUDIO = False


def load_workspace_defaults(defaults_path: Path | None = None) -> dict[str, str]:
    path = defaults_path or WORKSPACE_DEFAULTS_PATH
    defaults: dict[str, str] = {}
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                for key, value in payload.items():
                    if value not in (None, ""):
                        defaults[key] = str(value)
        except Exception:
            pass
    return defaults


@dataclass
class VideoJob:
    project_id: str
    location: str
    model: str
    prompt: str
    aspect_ratio: str
    duration_seconds: Optional[int]
    output_gcs_uri: Optional[str]
    poll_seconds: int
    project_dir: Optional[str]
    mongo_uri: Optional[str]
    mongo_db: Optional[str]
    user_id: Optional[str]
    project_env_id: Optional[str]
    resolution: str
    generate_audio: bool
    watermarked: bool


def load_project_manifest(project_dir: str | None) -> dict[str, object]:
    if not project_dir:
        return {}
    path = Path(project_dir).expanduser().resolve() / "project.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def as_bool(value: object | None, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def parse_args() -> VideoJob:
    parser = argparse.ArgumentParser(description="Generate a video with Vertex AI / Veo")
    parser.add_argument("--project-dir", default=None, help="Optional project workspace directory")
    parser.add_argument("--project-id", default=None, help="Google Cloud project ID")
    parser.add_argument("--location", default=None, help="Vertex AI location")
    parser.add_argument("--model", default=None, help="Veo model ID")
    parser.add_argument("--prompt", required=True, help="Text prompt for the video")
    parser.add_argument("--aspect-ratio", default="16:9", choices=["16:9", "9:16"], help="Video aspect ratio")
    parser.add_argument("--duration-seconds", type=int, default=None, help="Optional requested duration in seconds")
    parser.add_argument("--output-gcs-uri", default=None, help="Optional gs:// bucket/prefix for output")
    parser.add_argument("--resolution", default=None, help="Optional output resolution override")
    parser.add_argument("--generate-audio", action=argparse.BooleanOptionalAction, default=None, help="Generate audio for the video")
    parser.add_argument("--poll-seconds", type=int, default=15, help="Seconds between status checks")
    parser.add_argument("--mongo-uri", default=None, help="MongoDB connection string")
    parser.add_argument("--mongo-db", default=None, help="MongoDB database name")
    parser.add_argument("--user-id", default=None, help="MongoDB user ObjectId string")
    parser.add_argument("--project-env-id", default=None, help="MongoDB project id to link the media asset to")
    parser.add_argument("--watermarked", action="store_true", help="Mark the stored media asset as watermarked")

    args = parser.parse_args()
    manifest = load_project_manifest(args.project_dir)
    workspace_defaults = load_workspace_defaults()

    project_id = args.project_id or manifest.get("gcp_project_id") or os.getenv("GOOGLE_CLOUD_PROJECT")
    location = args.location or manifest.get("gcp_location") or os.getenv("GOOGLE_CLOUD_LOCATION", "global")
    model = args.model or workspace_defaults.get("veo_model") or manifest.get("veo_model") or DEFAULT_VEO_MODEL
    resolution = args.resolution or workspace_defaults.get("veo_resolution") or manifest.get("veo_resolution") or DEFAULT_VEO_RESOLUTION
    generate_audio = (
        args.generate_audio
        if args.generate_audio is not None
        else as_bool(workspace_defaults.get("veo_generate_audio"), default=as_bool(manifest.get("veo_generate_audio"), default=DEFAULT_VEO_GENERATE_AUDIO))
    )
    mongo_uri = args.mongo_uri or manifest.get("mongo_uri") or workspace_defaults.get("mongo_uri") or os.getenv("MONGODB_URI")
    mongo_db_value = args.mongo_db or manifest.get("mongo_db") or workspace_defaults.get("mongo_db") or os.getenv("MONGODB_DB")
    user_id_value = args.user_id or manifest.get("mongo_user_id") or workspace_defaults.get("mongo_user_id") or os.getenv("MONGODB_USER_ID")
    project_env_value = args.project_env_id or manifest.get("mongo_project_id")
    duration_seconds = args.duration_seconds

    if not project_id:
        parser.error("--project-id is required (or set GOOGLE_CLOUD_PROJECT, or use --project-dir with a manifest)")

    return VideoJob(
        project_id=str(project_id),
        location=str(location),
        model=str(model),
        prompt=args.prompt,
        aspect_ratio=args.aspect_ratio,
        duration_seconds=duration_seconds,
        output_gcs_uri=args.output_gcs_uri,
        poll_seconds=args.poll_seconds,
        project_dir=args.project_dir,
        mongo_uri=str(mongo_uri) if mongo_uri is not None else None,
        mongo_db=str(mongo_db_value) if mongo_db_value is not None else None,
        user_id=str(user_id_value) if user_id_value is not None else None,
        project_env_id=str(project_env_value) if project_env_value is not None else None,
        resolution=str(resolution),
        generate_audio=bool(generate_audio),
        watermarked=bool(args.watermarked),
    )


def clamp_veo_duration(seconds: float | int | None) -> int:
    if seconds is None:
        return 8
    val = float(seconds)
    if val <= 4.0:
        return 4
    if val <= 6.0:
        return 6
    return 8


def make_client(job: VideoJob) -> genai.Client:
    load_dotenv(ROOT / ".env")
    os.environ["GOOGLE_CLOUD_PROJECT"] = job.project_id
    os.environ["GOOGLE_CLOUD_LOCATION"] = job.location
    os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "True"
    cred_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if not cred_path or not Path(cred_path).exists():
        fallback_key = ROOT / "gcp-key.json"
        if fallback_key.exists():
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(fallback_key)
    elif cred_path and not Path(cred_path).is_absolute():
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str((ROOT / cred_path).resolve())
    return genai.Client(vertexai=True, project=job.project_id, location=job.location)


def run_video_job(job: VideoJob, *, emit_status: bool = True) -> dict[str, object]:
    client = make_client(job)

    clamped_duration = clamp_veo_duration(job.duration_seconds)
    config_kwargs: dict[str, object] = {
        "aspect_ratio": job.aspect_ratio,
        "output_gcs_uri": job.output_gcs_uri,
        "resolution": job.resolution,
        "generate_audio": job.generate_audio,
        "duration_seconds": clamped_duration,
    }

    config = GenerateVideosConfig(**config_kwargs)

    if emit_status:
        print(json.dumps({"status": "starting", "job": asdict(job)}, indent=2))

    max_retries = 5
    retry_delay = 10.0
    for attempt in range(1, max_retries + 1):
        try:
            operation = client.models.generate_videos(
                model=job.model,
                prompt=job.prompt,
                config=config,
            )
            break
        except Exception as exc:
            if "429" in str(exc) or "RESOURCE_EXHAUSTED" in str(exc):
                if attempt < max_retries:
                    print(f"[WARN] Veo API rate limit hit (429/RESOURCE_EXHAUSTED). Retrying in {retry_delay:.1f}s (Attempt {attempt}/{max_retries})...")
                    time.sleep(retry_delay)
                    retry_delay *= 1.5
                    continue
            raise
    operation_name = getattr(operation, "name", None)

    while not operation.done:
        time.sleep(job.poll_seconds)
        operation = client.operations.get(operation)
        operation_name = operation_name or getattr(operation, "name", None)
        if emit_status:
            print(json.dumps({"status": "polling", "done": operation.done}, indent=2))

    if not operation.response:
        raise RuntimeError("No response returned from operation")

    result = operation.result
    payload: dict[str, object] = {
        "status": "done",
        "model": job.model,
        "project_id": job.project_id,
        "location": job.location,
    }

    videos = []
    for index, video in enumerate(getattr(result, "generated_videos", []) or []):
        video_uri = getattr(getattr(video, "video", None), "uri", None) or job.output_gcs_uri
        videos.append({"index": index, "uri": video_uri})
    if videos:
        payload["videos"] = videos

    mongo_status: dict[str, object]
    settings = resolve_settings(job.mongo_uri, job.mongo_db, job.user_id)
    if settings is None or not job.project_env_id or operation_name is None:
        mongo_status = {
            "status": "skipped",
            "reason": "MongoDB settings are incomplete or the operation has no name",
        }
    else:
        client_mongo = None
        try:
            client_mongo, db = connect(settings.uri, settings.db_name)
            inserted = []
            mediaassets = db["mediaassets"]
            for index, video in enumerate(videos):
                operation_key = f"{operation_name}:{index}" if len(videos) > 1 else operation_name
                media_doc = build_mediaasset_doc(
                    prompt=job.prompt,
                    model=job.model,
                    aspect_ratio=job.aspect_ratio,
                    duration_seconds=clamped_duration,
                    local_path=None,
                    gcs_uri=video.get("uri"),
                    operation_name=operation_key,
                    project_env_id=job.project_env_id,
                    user_id=settings.user_id,
                    batch_queue_item_id=None,
                    watermarked=job.watermarked,
                    timestamp=utc_now(),
                )
                result_db = upsert_mediaasset(mediaassets, media_doc)
                inserted_id = result_db.upserted_id
                if inserted_id is None:
                    existing = mediaassets.find_one({"operationName": operation_key}, {"_id": 1, "id": 1, "batchQueueItemId": 1})
                    if existing is None:
                        raise RuntimeError(f"MongoDB mediaasset lookup failed for {operation_key}")
                    inserted_id = existing["_id"]
                inserted_id_str = str(inserted_id)
                mediaassets.update_one(
                    {"operationName": operation_key},
                    {
                        "$set": {
                            "id": inserted_id_str,
                            "batchQueueItemId": inserted_id_str,
                            "isCharacter": False,
                            "localPath": f"/api/videos/{inserted_id_str}.mp4",
                        }
                    },
                )
                inserted.append({
                    "operationName": operation_key,
                    "matched": result_db.matched_count,
                    "modified": result_db.modified_count,
                    "upserted_id": inserted_id_str if result_db.upserted_id is not None else None,
                    "localPath": f"/api/videos/{inserted_id_str}.mp4",
                })
            mongo_status = {
                "status": "written",
                "database": settings.db_name,
                "projectEnvId": job.project_env_id,
                "mediaassets": inserted,
            }
        finally:
            if client_mongo is not None:
                client_mongo.close()

    payload["mongo"] = mongo_status
    return payload


def main() -> int:
    job = parse_args()
    try:
        payload = run_video_job(job, emit_status=True)
    except Exception as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, indent=2), file=sys.stderr)
        return 1

    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
