import json
from pymilvus import MilvusClient

c = MilvusClient(uri='http://127.0.0.1:19530', token='root:Milvus')
name = 'oncall_knowledge_v1_1536'
desc = c.describe_collection(name)
print('fields:', [f['name'] for f in desc['fields']])
print('num_entities:', desc.get('num_entities'), '| stats:', c.get_collection_stats(name))
res = c.query(collection_name=name, filter='', output_fields=['document_id', 'version_id', 'title', 'page_range'], limit=1000)
print('query count:', len(res))
titles = {}
for r in res:
    titles[r['title']] = titles.get(r['title'], 0) + 1
print('by title:', json.dumps(titles, ensure_ascii=False))
