"""Count actually reporting metric time series, excluding stale preflight Task IDs."""
import datetime as dt
import json
from pathlib import Path
from aws_session import aws, private_json, ROOT

out=ROOT/'.local/cost-review'
inventory=json.loads((out/'inventory.json').read_text())
metrics=inventory['insights_metrics']['Metrics']
queries=[{'Id':f'm{i}','MetricStat':{'Metric':m,'Period':3600,'Stat':'SampleCount'},'ReturnData':True} for i,m in enumerate(metrics)]
end=dt.datetime.now(dt.timezone.utc).replace(second=0,microsecond=0)-dt.timedelta(minutes=5)
response=aws('cloudwatch','get-metric-data','--metric-data-queries',private_json('cost-active-metrics.json',queries),
             '--start-time',(end-dt.timedelta(hours=1)).isoformat(),'--end-time',end.isoformat())
active=[];inactive=[]
for r in response['MetricDataResults']:
    i=int(r['Id'][1:]);(active if sum(r.get('Values',[]))>0 else inactive).append(metrics[i])
groups={}
for m in active:
    names=','.join(sorted(d['Name'] for d in m.get('Dimensions',[])))
    groups[names]=groups.get(names,0)+1
result={'active':active,'inactive':inactive,'active_count':len(active),'inactive_count':len(inactive),
        'dimension_groups':groups,'query_statuses':sorted(set(x['StatusCode'] for x in response['MetricDataResults']))}
(out/'active-metrics.json').write_text(json.dumps(result,indent=2))
print(json.dumps({k:v for k,v in result.items() if k not in ['active','inactive']},indent=2))
