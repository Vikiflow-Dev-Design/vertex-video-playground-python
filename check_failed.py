import pymongo
from datetime import datetime, timezone

client = pymongo.MongoClient('mongodb://victor:victoruche22123vic@76.13.42.74:27017/?directConnection=true')
db = client['video-studio']

from datetime import datetime, timezone, timedelta

ten_mins_ago = datetime.now(timezone.utc) - timedelta(minutes=10)

res = db['queuejobs'].update_many(
    {'projectEnvId': 'project-1784641824147', 'status': 'generating', 'updatedAt': {'$lt': ten_mins_ago}},
    {'$set': {'status': 'queued', 'error': None, 'updatedAt': datetime.now(timezone.utc)}}
)
print(f"Reset {res.modified_count} old generating jobs back to queued.")

res = db['queuejobs'].update_many(
    {'projectEnvId': 'project-1784641824147', 'status': 'generating'},
    {'$set': {'status': 'queued', 'operationName': None, 'error': None}}
)
print(f"Cleared operationName and reset {res.modified_count} jobs back to queued.")

queued = db['queuejobs'].count_documents({'projectEnvId': 'project-1784641824147', 'status': 'queued'})
generating = db['queuejobs'].count_documents({'projectEnvId': 'project-1784641824147', 'status': 'generating'})
assets = db['mediaassets'].count_documents({'projectEnvId': 'project-1784641824147'})
print(f"Current Breakdown:\n  - Queued: {queued}\n  - Generating: {generating}\n  - Succeeded Assets: {assets}")
