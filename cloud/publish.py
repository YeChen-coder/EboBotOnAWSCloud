"""Publish the two tested images after the infrastructure exists."""
import argparse
import base64
import json
import os
import subprocess
from aws_session import aws, verify_account, ROOT, ACCOUNT, REGION

def publish(tag):
    verify_account()
    registry=f'{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com'
    auth=aws('ecr','get-authorization-token','--registry-ids',ACCOUNT)['authorizationData'][0]
    username,password=base64.b64decode(auth['authorizationToken']).decode().split(':',1)
    directory=ROOT/'.local/docker-publish'
    directory.mkdir(parents=True,exist_ok=True)
    env={**os.environ,'DOCKER_CONFIG':str(directory)}
    login=subprocess.run(['docker','login','--username',username,'--password-stdin',registry],input=password,
                         text=True,capture_output=True,env=env)
    if login.returncode: raise RuntimeError('ECR Docker login failed')
    images={}
    try:
        for service in ['engine','assistant']:
            target=f'{registry}/ebo-cloud/{service}:{tag}'
            subprocess.run(['docker','tag',f'ebo-cloud/{service}:{tag}',target],check=True,env=env)
            subprocess.run(['docker','push',target],check=True,env=env)
            digest=aws('ecr','describe-images','--repository-name','ebo-cloud/'+service,
                       '--image-ids','imageTag='+tag)['imageDetails'][0]['imageDigest']
            images[service]=f'{registry}/ebo-cloud/{service}@{digest}'
        (ROOT/'.local/published-images.json').write_text(json.dumps(images,indent=2),encoding='utf-8')
        print(json.dumps(images,indent=2))
    finally:
        subprocess.run(['docker','logout',registry],env=env,capture_output=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--tag',default='20260910-01')
    publish(parser.parse_args().tag)
