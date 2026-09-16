"""Read-only inventory and usage measurement for an AWS cost estimate."""
import concurrent.futures
import datetime as dt
import json
import time
from aws_session import aws, verify_account, private_json, ROOT, EFS_ID, require_setting

verify_account()
efs_id=require_setting('EBO_EFS_ID',EFS_ID)
out=ROOT/'.local/cost-review'
out.mkdir(parents=True,exist_ok=True)
end=dt.datetime.now(dt.timezone.utc).replace(second=0,microsecond=0)
start=end-dt.timedelta(hours=3)
calls={
 'resources':('cloudformation','list-stack-resources','--stack-name','ebo-cloud-lab'),
 'services':('ecs','describe-services','--cluster','ebo-cloud-lab','--services','ebo-cloud-lab'),
 'clusters':('ecs','describe-clusters','--clusters','ebo-cloud-lab','--include','SETTINGS'),
 'application_groups':('logs','describe-log-groups','--log-group-name-prefix','/ebo-cloud/'),
 'performance_groups':('logs','describe-log-groups','--log-group-name-prefix','/aws/ecs/containerinsights/ebo-cloud-lab/'),
 'efs':('efs','describe-file-systems','--file-system-id',efs_id),
 'efs_backup':('efs','describe-backup-policy','--file-system-id',efs_id),
 'backup_vaults':('backup','list-backup-vaults'),
 'engine_images':('ecr','describe-images','--repository-name','ebo-cloud/engine'),
 'assistant_images':('ecr','describe-images','--repository-name','ebo-cloud/assistant'),
 'ecr_scanning':('ecr','get-registry-scanning-configuration'),
 'insights_metrics':('cloudwatch','list-metrics','--namespace','ECS/ContainerInsights','--dimensions','Name=ClusterName,Value=ebo-cloud-lab'),
 'app_metrics':('cloudwatch','list-metrics','--namespace','EBO/Diagnostics'),
}
def fetch(item):
    name,args=item
    try:
        data=aws(*args)
        (out/(name+'.json')).write_text(json.dumps(data,indent=2),encoding='utf-8')
        return name,data
    except Exception as exc: return name,{'error':str(exc)}
with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
    data=dict(pool.map(fetch,calls.items()))
arns=aws('ecs','list-tasks','--cluster','ebo-cloud-lab','--service-name','ebo-cloud-lab').get('taskArns',[])
data['tasks']=aws('ecs','describe-tasks','--cluster','ebo-cloud-lab','--tasks',*arns) if arns else {}
queries=[]
for i,group in enumerate(['/ebo-cloud/engine','/ebo-cloud/assistant','/aws/ecs/containerinsights/ebo-cloud-lab/performance']):
    for suffix,metric in [('bytes','IncomingBytes'),('events','IncomingLogEvents')]:
        queries.append({'Id':f'g{i}_{suffix}','MetricStat':{'Metric':{'Namespace':'AWS/Logs','MetricName':metric,
            'Dimensions':[{'Name':'LogGroupName','Value':group}]},'Period':60,'Stat':'Sum'},'ReturnData':True})
for i,metric in enumerate(['CPUUtilization','MemoryUtilization']):
    queries.append({'Id':f'ecs{i}','MetricStat':{'Metric':{'Namespace':'AWS/ECS','MetricName':metric,
      'Dimensions':[{'Name':'ClusterName','Value':'ebo-cloud-lab'},{'Name':'ServiceName','Value':'ebo-cloud-lab'}]},
      'Period':60,'Stat':'Average'},'ReturnData':True})
data['usage']=aws('cloudwatch','get-metric-data','--metric-data-queries',private_json('cost-metric-queries.json',queries),
                  '--start-time',start.isoformat(),'--end-time',end.isoformat(),'--scan-by','TimestampAscending')
data['sample_task_performance']=aws('logs','filter-log-events','--log-group-name','/aws/ecs/containerinsights/ebo-cloud-lab/performance',
   '--start-time',str(int((time.time()-900)*1000)),'--filter-pattern','{ $.Type = "Task" }')
for vault in data.get('backup_vaults',{}).get('BackupVaultList',[]):
    data['recovery_points_'+vault['BackupVaultName']]=aws('backup','list-recovery-points-by-backup-vault','--backup-vault-name',vault['BackupVaultName'])
data['measurement']={'start':start.isoformat(),'end':end.isoformat(),'scope':'EBO deployment only'}
(out/'inventory.json').write_text(json.dumps(data,indent=2),encoding='utf-8')
print(json.dumps({'saved':str(out/'inventory.json'),'errors':{k:v for k,v in data.items() if isinstance(v,dict) and 'error' in v},
                 'metrics_count':len(data.get('insights_metrics',{}).get('Metrics',[])),
                 'task_count':len(data['tasks'].get('tasks',[]))}))
