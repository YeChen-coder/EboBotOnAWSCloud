"""Explicit deployment phases; live cutover is a separate operation."""
import argparse
import json
import subprocess
from aws_session import (aws, verify_account, private_json, require_setting, ROOT, ACCOUNT,
                         REGION, SOURCE_ROOT, EBO_APP_ID)

STACK='ebo-cloud-lab'
SOURCE=SOURCE_ROOT

def outputs():
    stack=aws('cloudformation','describe-stacks','--stack-name',STACK)['Stacks'][0]
    return {entry['OutputKey']:entry['OutputValue'] for entry in stack.get('Outputs',[])}

def provision(tag):
    import make_template
    template=private_json('cloudformation-template.json',make_template.template)
    aws('cloudformation','validate-template','--template-body',template)
    params=[{'ParameterKey':name+'Image','ParameterValue':f'{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com/ebo-cloud/{service}:{tag}'}
            for name,service in [('Engine','engine'),('Assistant','assistant')]]
    params.append({'ParameterKey':'DesiredCount','ParameterValue':'0'})
    result=aws('cloudformation','create-stack','--stack-name',STACK,'--template-body',template,
               '--parameters',private_json('stack-parameters.json',params),'--capabilities','CAPABILITY_IAM',
               '--tags','Key=Project,Value=ebo-cloud','Key=Environment,Value=lab')
    print(json.dumps(result))

def prepare():
    # Capture Compose's interpolation without ever printing secrets or prompt content.
    compose=subprocess.run(['docker','compose','--profile','assistant','config','--format','json'],
                           cwd=SOURCE,capture_output=True,text=True,encoding='utf-8',check=True)
    services=json.loads(compose.stdout)['services']
    options=json.loads((SOURCE/'ebo-data/options.json').read_text(encoding='utf-8-sig'))
    choices=json.loads((SOURCE/'ebo-data/ui_choices.json').read_text(encoding='utf-8-sig'))
    assistant=services['realtime-assistant']['environment']
    token=(SOURCE/'ebo-data/api_token').read_text().strip()
    if not token or token!=assistant.get('EBO_API_TOKEN'):
        raise RuntimeError('Engine and assistant API tokens disagree')
    if not assistant.get('OPENAI_API_KEY'): raise RuntimeError('Missing OpenAI API key')
    engine=services['ebo-engine']['environment']
    engine['EBO_API_TOKEN']=token
    engine['EBO_APP_ID']=require_setting('EBO_APP_ID',EBO_APP_ID)
    # Keep all resolved tuning, use cloud topology, do not copy historical family recordings.
    values={'Engine':{'environment':engine,'options':options,'ui_choices':choices},
            'Assistant':{'environment':assistant}}
    for name,value in values.items():
        private_json(name.lower()+'-bootstrap.json',value)
    print('Local runtime configuration prepared; API token consistency verified. No transcript/audio history copied.')

def configure():
    prepare()
    out=outputs()
    for name in ['Engine','Assistant']:
        file='file://'+(ROOT/'.local'/(name.lower()+'-bootstrap.json')).as_posix()
        aws('secretsmanager','put-secret-value','--secret-id',out[name+'Secret'],'--secret-string',file)
    print('Runtime configuration uploaded to Secrets Manager.')

def status():
    stack=aws('cloudformation','describe-stacks','--stack-name',STACK)['Stacks'][0]
    print('Stack:',stack['StackStatus'])
    if stack['StackStatus'].endswith('IN_PROGRESS') or 'FAILED' in stack['StackStatus'] or 'ROLLBACK' in stack['StackStatus']:
        events=aws('cloudformation','describe-stack-events','--stack-name',STACK)['StackEvents']
        for event in events[:12]:
            print(event['LogicalResourceId'],event['ResourceStatus'],event.get('ResourceStatusReason',''))
    else:
        out=outputs()
        (ROOT/'.local/deployment.json').write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps(out,indent=2))

def set_capacity(count):
    if count == 1:
        for container in ['ebo-ai-home-ebo-engine','ebo-ai-home-realtime-assistant']:
            inspected=subprocess.run(['docker','inspect','--format','{{.State.Running}}',container],
                                     capture_output=True,text=True,check=True)
            if inspected.stdout.strip()!='false':
                raise RuntimeError('Stop local business containers before starting the cloud task')
    images=json.loads((ROOT/'.local/published-images.json').read_text(encoding='utf-8'))
    params=[{'ParameterKey':name+'Image','ParameterValue':images[service]}
            for name,service in [('Engine','engine'),('Assistant','assistant')]]
    params.append({'ParameterKey':'DesiredCount','ParameterValue':str(count)})
    result=aws('cloudformation','update-stack','--stack-name',STACK,'--use-previous-template',
               '--parameters',private_json('update-parameters.json',params),'--capabilities','CAPABILITY_IAM')
    print(json.dumps(result))

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('phase',choices=['provision','prepare','configure','status','start','stop'])
    parser.add_argument('--tag',default='20260910-01')
    args=parser.parse_args()
    if args.phase!='prepare': verify_account()
    {'provision':lambda:provision(args.tag),'prepare':prepare,'configure':configure,'status':status,
     'start':lambda:set_capacity(1),'stop':lambda:set_capacity(0)}[args.phase]()
