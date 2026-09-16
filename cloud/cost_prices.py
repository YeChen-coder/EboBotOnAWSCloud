"""Download official AWS regional public price lists for the cost report."""
import concurrent.futures
import json
from pathlib import Path
from urllib.request import urlopen

OUT=Path(__file__).resolve().parents[1]/'.local/cost-review/prices'
OUT.mkdir(parents=True,exist_ok=True)
SERVICES=['AmazonECS','AmazonCloudWatch','AmazonEFS','AWSBackup','AmazonECR','AWSSecretsManager','AmazonVPC','AWSDataTransfer']
def fetch(service):
    url=f'https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/{service}/current/ca-central-1/index.json'
    try:
        with urlopen(url,timeout=45) as response: data=json.load(response)
        (OUT/(service+'.json')).write_text(json.dumps(data),encoding='utf-8')
        return {'service':service,'products':len(data['products']),'publicationDate':data.get('publicationDate'),'source':url}
    except Exception as exc: return {'service':service,'error':str(exc)}
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool: results=list(pool.map(fetch,SERVICES))
(OUT/'sources.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
print(json.dumps(results,indent=2))
