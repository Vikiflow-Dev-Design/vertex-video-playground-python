import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mongo_store import connect
from scripts.generate_and_cut_project_videos import parse_prompt_file

project_dir = ROOT / "video_projects" / "the-entire-history-of-baghdad"
project_manifest = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
project_env_id = project_manifest.get("mongo_project_id") or "project-1784894724574"
mongo_uri = project_manifest.get("mongo_uri") or "mongodb://victor:victoruche22123vic@76.13.42.74:27017/?directConnection=true"
mongo_db = project_manifest.get("mongo_db") or "video-studio"

client, db = connect(mongo_uri, mongo_db)

prompt_blocks = parse_prompt_file(project_dir / "prompts" / "visual_prompts.md")
print(f"Total prompt blocks in project: {len(prompt_blocks)}")

assets = list(db["mediaassets"].find({"projectEnvId": project_env_id}))
print(f"Total mediaassets in DB for projectEnvId '{project_env_id}': {len(assets)}")

# Map prompt to asset
found_clips = {}
for block in prompt_blocks:
    # Match by prompt prefix or exact text
    prompt_num_str = f"{block.clip_number:03d}:"
    matched_asset = None
    for asset in assets:
        asset_prompt = asset.get("prompt") or ""
        if asset_prompt.startswith(prompt_num_str) or block.prompt_text in asset_prompt or asset_prompt in block.prompt_text:
            matched_asset = asset
            break
    if matched_asset:
        found_clips[block.clip_number] = matched_asset
        print(f"  [DONE] Clip {block.clip_number:03d}: GCS={matched_asset.get('gcsUri')} | ID={matched_asset['_id']}")
    else:
        print(f"  [MISSING] Clip {block.clip_number:03d}")

print(f"\nSummary: {len(found_clips)} / {len(prompt_blocks)} clips found in MongoDB mediaassets.")
client.close()
