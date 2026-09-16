"""Compact live deployment verification; saves a shareable health summary."""
import json
import time
from aws_session import aws, verify_account, ROOT, ACCOUNT, REGION

verify_account()
service=aws('ecs','describe-services','--cluster','ebo-cloud-lab','--services','ebo-cloud-lab')['services'][0]
arns=aws('ecs','list-tasks','--cluster','ebo-cloud-lab','--service-name','ebo-cloud-lab').get('taskArns',[])
tasks=aws('ecs','describe-tasks','--cluster','ebo-cloud-lab','--tasks',*arns)['tasks'] if arns else []
summary={'checked_at_epoch':time.time(),'region':REGION,'account':ACCOUNT,
 'service':{k:service.get(k) for k in ['serviceArn','desiredCount','runningCount','pendingCount','taskDefinition']},
 'deployments':[{k:d.get(k) for k in ['status','rolloutState','rolloutStateReason']} for d in service['deployments']],
 'recent_service_events':service.get('events',[])[:3],'tasks':[]}
for task in tasks:
    item={k:task.get(k) for k in ['taskArn','lastStatus','healthStatus','cpu','memory','availabilityZone','platformVersion']}
    item['containers']=[{k:c.get(k) for k in ['name','lastStatus','healthStatus','exitCode','reason','imageDigest']} for c in task['containers']]
    item['latest_health']={}
    taskid=task['taskArn'].rsplit('/',1)[-1]
    for name,group in [('ebo-engine','engine'),('realtime-assistant','assistant')]:
        events=aws('logs','filter-log-events','--log-group-name','/ebo-cloud/'+group,
                   '--log-stream-name-prefix',name+'/'+name+'/'+taskid,
                   '--start-time',str(int((time.time()-900)*1000))).get('events',[])
        health=[];errors=[]
        for e in events:
            try: row=json.loads(e['message'])
            except json.JSONDecodeError: continue
            if row.get('event')=='health.snapshot': health.append(row)
            if row.get('severity') in ['ERROR','WARNING']: errors.append(row)
        item['latest_health'][name]=health[-1] if health else None
        item.setdefault('recent_warnings',{})[name]=errors[-4:]
    summary['tasks'].append(item)
(ROOT/'.local/live-verification.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding='utf-8')
print(json.dumps(summary,indent=2,ensure_ascii=False))
