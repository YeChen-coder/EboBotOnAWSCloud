"""Read-only inventory, usage and public regional AWS price-list capture."""
import concurrent.futures
import datetime as dt
import json
from pathlib import Path
import subprocess
import time
from urllib.request import urlopen
from aws_session import aws, verify_account, ROOT, environment, ACCOUNT, REGION, EFS_ID, require_setting

OUT=ROOT/'.local/cost-audit'
OUT.mkdir(parents=True,exist_ok=True)
def save(name,obj):
    (OUT/(name+'.json')).write_text(json.dumps(obj,indent=2,ensure_ascii=False),encoding='utf-8')
    return obj

def region_call(region,*args):
    cli=ROOT/'.tools/aws-cli/Amazon/AWSCLIV2/aws.exe'
    result=subprocess.run([str(cli),*args,'--region',region,'--profile','ebo-cloud','--output','json'],
                          capture_output=True,text=True,encoding='utf-8',env=environment())
    if result.returncode:
        save('error-'+args[0],{'error':result.stderr[:1500]})
        return {'unavailable':True}
    return json.loads(result.stdout)

def usage():
    verify_account()
    efs_id=require_setting('EBO_EFS_ID',EFS_ID)
    now=dt.datetime.now(dt.timezone.utc)
    # Complete one-minute buckets, excluding last three minutes of ingestion delay.
    end=now.replace(second=0,microsecond=0)-dt.timedelta(minutes=3)
    start=end-dt.timedelta(minutes=15)
    save('window',{'observed_at':now.isoformat(),'start':start.isoformat(),'end':end.isoformat(),'minutes':15})
    jobs={
      'resources':lambda:aws('cloudformation','list-stack-resources','--stack-name','ebo-cloud-lab'),
      'service':lambda:aws('ecs','describe-services','--cluster','ebo-cloud-lab','--services','ebo-cloud-lab'),
      'cluster':lambda:aws('ecs','describe-clusters','--clusters','ebo-cloud-lab','--include','SETTINGS'),
      'efs':lambda:aws('efs','describe-file-systems','--file-system-id',efs_id),
      'app-log-groups':lambda:aws('logs','describe-log-groups','--log-group-name-prefix','/ebo-cloud/'),
      'performance-log-group':lambda:aws('logs','describe-log-groups','--log-group-name-prefix','/aws/ecs/containerinsights/ebo-cloud-lab/'),
      'container-metrics':lambda:aws('cloudwatch','list-metrics','--namespace','ECS/ContainerInsights','--dimensions','Name=ClusterName,Value=ebo-cloud-lab'),
      'app-metrics':lambda:aws('cloudwatch','list-metrics','--namespace','EBO/Diagnostics'),
      'engine-images':lambda:aws('ecr','describe-images','--repository-name','ebo-cloud/engine'),
      'assistant-images':lambda:aws('ecr','describe-images','--repository-name','ebo-cloud/assistant'),
      'backup-points':lambda:aws('backup','list-recovery-points-by-resource','--resource-arn',f'arn:aws:elasticfilesystem:{REGION}:{ACCOUNT}:file-system/{efs_id}'),
      'efs-mounts':lambda:aws('efs','describe-mount-targets','--file-system-id',efs_id),
    }
    for label,group in [('engine','/ebo-cloud/engine'),('assistant','/ebo-cloud/assistant'),('performance','/aws/ecs/containerinsights/ebo-cloud-lab/performance')]:
        for metric in ['IncomingBytes','IncomingLogEvents']:
            jobs[label+'-'+metric]=lambda group=group,metric=metric:aws('cloudwatch','get-metric-statistics',
              '--namespace','AWS/Logs','--metric-name',metric,'--dimensions','Name=LogGroupName,Value='+group,
              '--start-time',start.isoformat(),'--end-time',end.isoformat(),'--period','60','--statistics','Sum')
    def perform(entry):
        name,call=entry
        try:
            value=call();save(name,value)
            return name,'ok'
        except Exception as exc:
            save(name,{'unavailable':type(exc).__name__});return name,'unavailable'
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for name,status in pool.map(perform,jobs.items()): print(name,status,flush=True)
    tasks=aws('ecs','list-tasks','--cluster','ebo-cloud-lab','--service-name','ebo-cloud-lab')['taskArns']
    if tasks: save('tasks',aws('ecs','describe-tasks','--cluster','ebo-cloud-lab','--tasks',*tasks))
    groups=['/ebo-cloud/engine','/ebo-cloud/assistant','/aws/ecs/containerinsights/ebo-cloud-lab/performance']
    for label,group in zip(['engine','assistant','performance'],groups):
        result=aws('logs','filter-log-events','--log-group-name',group,'--start-time',str(int(start.timestamp()*1000)),
                   '--end-time',str(int(end.timestamp()*1000)))
        # Persist only statistical summaries and whitelisted performance fields; no app text.
        events=result.get('events',[])
        categories={};perf=[]
        for event in events:
            try: row=json.loads(event['message'])
            except json.JSONDecodeError: row={}
            cat=row.get('event',row.get('Type','text'));categories[cat]=categories.get(cat,0)+1
            if row.get('Type')=='Task':
                perf.append({k:v for k,v in row.items() if k in ['TaskId','Timestamp','CpuUtilized','MemoryUtilized','NetworkRxBytes','NetworkTxBytes','NetworkRxDropped','NetworkTxDropped']})
        save(label+'-log-sample',{'events':len(events),'utf8_message_bytes':sum(len(e['message'].encode('utf-8')) for e in events),
           'categories':categories,'performance':perf,'first_ms':min((e['timestamp'] for e in events),default=None),
           'last_ms':max((e['timestamp'] for e in events),default=None)})
    # A single account-level bill query (up to $0.01), not a project-attributed invoice.
    tomorrow=(now.date()+dt.timedelta(days=1)).isoformat()
    save('cost-explorer',region_call('us-east-1','ce','get-cost-and-usage',
      '--time-period','Start='+now.replace(day=1).date().isoformat()+',End='+tomorrow,
      '--granularity','DAILY','--metrics','UnblendedCost','--group-by','Type=DIMENSION,Key=SERVICE'))

def prices():
    services=['AmazonECS','AmazonCloudWatch','AmazonEFS','AmazonEC2ContainerRegistry','AWSSecretsManager','AmazonVPC','AWSBackup','AWSDataTransfer']
    def fetch(service):
        url=f'https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/{service}/current/ca-central-1/index.json'
        try:
            with urlopen(url,timeout=90) as response: data=json.load(response)
            save('prices-'+service,data)
            return service,{'url':url,'published':data.get('publicationDate'),'products':len(data.get('products',{}))}
        except Exception as exc:return service,{'url':url,'error':str(exc)}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        result=dict(pool.map(fetch,services))
    save('price-sources',result);print(json.dumps(result),flush=True)

if __name__=='__main__':
    import sys
    {'usage':usage,'prices':prices}[sys.argv[1]]()
