"""Bounded local evidence collection and explicit ECS service replacement.

No shell execution or robot control API is exposed to an LLM. The AWS login
profile is an operator credential; production agents need a separate IAM role.
"""
import argparse
import datetime
import json
from pathlib import Path
import sqlite3
import time
import uuid
from aws_session import aws, verify_account, ROOT, ACCOUNT, REGION

CLUSTER=SERVICE='ebo-cloud-lab'
GROUPS=['/ebo-cloud/engine','/ebo-cloud/assistant','/aws/ecs/containerinsights/ebo-cloud-lab/performance']

def collect(minutes):
    end=int(time.time()*1000)
    start=end-minutes*60000
    directory=ROOT/'.local/diagnostics'
    directory.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(directory/'evidence.sqlite')
    db.execute('CREATE TABLE IF NOT EXISTS logs (id TEXT PRIMARY KEY, timestamp INTEGER, log_group TEXT, stream TEXT, message TEXT)')
    failures=[]
    for group in GROUPS:
        token=None
        for _ in range(100):
            args=['logs','filter-log-events','--log-group-name',group,'--start-time',str(start),'--end-time',str(end),'--no-paginate']
            if token: args+=['--next-token',token]
            try: result=aws(*args)
            except RuntimeError:
                failures.append({'log_group':group,'error':'fetch_failed; details in private aws-last-error.txt'})
                break
            with db:
                db.executemany('INSERT OR IGNORE INTO logs VALUES (?,?,?,?,?)',[
                    (group+':'+e['eventId'],e['timestamp'],group,e['logStreamName'],e['message']) for e in result.get('events',[])])
            next_token=result.get('nextToken')
            if not next_token or next_token==token: break
            token=next_token
        else: failures.append({'log_group':group,'error':'page_limit_reached'})
    evidence=directory/(datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-evidence.jsonl')
    with evidence.open('w',encoding='utf-8') as output:
        for event_id,stamp,group,stream,message in db.execute('SELECT * FROM logs WHERE timestamp>=? AND timestamp<=? ORDER BY timestamp,id',(start,end)):
            try: payload=json.loads(message)
            except json.JSONDecodeError: payload={'message':message}
            output.write(json.dumps({'evidence_id':event_id,'timestamp_ms':stamp,'log_group':group,'stream':stream,
                                      'trust':'untrusted_observation','payload':payload},ensure_ascii=False)+'\n')
    db.close()
    state=aws('ecs','describe-services','--cluster',CLUSTER,'--services',SERVICE)
    tasks=aws('ecs','list-tasks','--cluster',CLUSTER,'--service-name',SERVICE).get('taskArns',[])
    details=aws('ecs','describe-tasks','--cluster',CLUSTER,'--tasks',*tasks) if tasks else {'tasks':[]}
    stopped=aws('ecs','list-tasks','--cluster',CLUSTER,'--service-name',SERVICE,'--desired-status','STOPPED').get('taskArns',[])[:10]
    stopped_details=aws('ecs','describe-tasks','--cluster',CLUSTER,'--tasks',*stopped) if stopped else {'tasks':[]}
    metrics=[]
    for metric in ['CPUUtilization','MemoryUtilization']:
        metrics.append(aws('cloudwatch','get-metric-statistics','--namespace','AWS/ECS','--metric-name',metric,
          '--dimensions','Name=ClusterName,Value='+CLUSTER,'Name=ServiceName,Value='+SERVICE,
          '--start-time',datetime.datetime.fromtimestamp(start/1000,datetime.timezone.utc).isoformat(),
          '--end-time',datetime.datetime.fromtimestamp(end/1000,datetime.timezone.utc).isoformat(),
          '--period','60','--statistics','Average','Maximum'))
    snapshot=evidence.with_suffix('.snapshot.json')
    snapshot.write_text(json.dumps({'account':ACCOUNT,'region':REGION,'window':[start,end],
        'services':state,'tasks':details,'recent_stopped_tasks':stopped_details,'metrics':metrics,
        'collection_errors':failures,'evidence_file':str(evidence)},indent=2),encoding='utf-8')
    print(json.dumps({'evidence':str(evidence),'snapshot':str(snapshot),'collection_errors':failures}))

def replace_task(expected_task,reason):
    current=aws('ecs','list-tasks','--cluster',CLUSTER,'--service-name',SERVICE).get('taskArns',[])
    if current!=[expected_task]: raise RuntimeError('Target changed or is not the only running service task')
    services=aws('ecs','describe-services','--cluster',CLUSTER,'--services',SERVICE)['services']
    if len(services)!=1 or services[0]['desiredCount']!=1:
        raise RuntimeError('Replacement requires one desired task')
    config=services[0]['deploymentConfiguration']
    if config['maximumPercent']!=100 or config['minimumHealthyPercent']!=0:
        raise RuntimeError('Deployment could overlap robot connections')
    if any(d['rolloutState']=='IN_PROGRESS' for d in services[0].get('deployments',[])):
        raise RuntimeError('Deployment already running')
    directory=ROOT/'.local/diagnostics'
    directory.mkdir(parents=True,exist_ok=True)
    action_id=str(uuid.uuid4())
    audit={'action_id':action_id,'timestamp':time.time(),'action':'replace_task','expected_task':expected_task,'reason':reason,'state':'requested'}
    with (directory/'actions.jsonl').open('a',encoding='utf-8') as out: out.write(json.dumps(audit)+'\n')
    aws('ecs','update-service','--cluster',CLUSTER,'--service',SERVICE,'--force-new-deployment')
    audit['state']='submitted'
    with (directory/'actions.jsonl').open('a',encoding='utf-8') as out: out.write(json.dumps(audit)+'\n')
    print(json.dumps(audit))

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    read=sub.add_parser('collect'); read.add_argument('--minutes',type=int,default=30)
    action=sub.add_parser('replace-task'); action.add_argument('--expected-task',required=True)
    action.add_argument('--reason',required=True); action.add_argument('--execute',action='store_true',required=True)
    args=parser.parse_args()
    verify_account()
    if args.command=='collect':
        if not 1<=args.minutes<=1440: parser.error('minutes must be 1..1440')
        collect(args.minutes)
    else: replace_task(args.expected_task,args.reason)
