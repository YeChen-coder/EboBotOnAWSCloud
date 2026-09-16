"""One Fargate task that validates bootstrap and EFS without starting business services."""
import argparse
import json
from aws_session import aws, verify_account, private_json, ROOT

def launch():
    service=aws('ecs','describe-services','--cluster','ebo-cloud-lab','--services','ebo-cloud-lab')['services'][0]
    if service['desiredCount']!=0: raise RuntimeError('Preflight requires stopped cloud service')
    script="import sys,json,pathlib;sys.path.insert(0,'/cloud');import runtime;runtime.bootstrap();p=pathlib.Path('/data/.cloud-preflight');p.write_text('ok');assert p.read_text()=='ok';p.unlink();print(json.dumps({'event':'preflight.ok','service':runtime.SERVICE,'efs_write':True,'bootstrap':True}))"
    overrides={'containerOverrides':[{'name':name,'command':['python','-c',script]} for name in ['ebo-engine','realtime-assistant']]}
    result=aws('ecs','run-task','--cluster','ebo-cloud-lab','--task-definition',service['taskDefinition'],
        '--launch-type','FARGATE','--platform-version','1.4.0','--count','1','--started-by','ebo-cloud-preflight',
        '--network-configuration',private_json('preflight-network.json',service['networkConfiguration']),
        '--overrides',private_json('preflight-overrides.json',overrides))
    if result.get('failures'): raise RuntimeError(str(result['failures']))
    arn=result['tasks'][0]['taskArn']
    (ROOT/'.local/preflight-task.txt').write_text(arn)
    print(arn)

def status():
    arn=(ROOT/'.local/preflight-task.txt').read_text()
    task=aws('ecs','describe-tasks','--cluster','ebo-cloud-lab','--tasks',arn)['tasks'][0]
    print(json.dumps({key:task.get(key) for key in ['taskArn','lastStatus','stoppedReason','stopCode']}))
    print(json.dumps([{key:c.get(key) for key in ['name','lastStatus','exitCode','reason']} for c in task['containers']]))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['launch','status']);args=parser.parse_args()
    verify_account();{'launch':launch,'status':status}[args.command]()
