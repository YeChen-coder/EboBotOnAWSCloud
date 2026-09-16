"""Generate the reviewable CloudFormation template without extra dependencies."""
import json
from pathlib import Path

ref = lambda name: {'Ref': name}
att = lambda name, attr: {'Fn::GetAtt': [name, attr]}
sub = lambda value: {'Fn::Sub': value}
R = {}
def add(name, kind, **props):
    R[name] = {'Type': 'AWS::' + kind, 'Properties': props}
    return ref(name)

vpc = add('Vpc', 'EC2::VPC', CidrBlock='10.87.0.0/16', EnableDnsSupport=True, EnableDnsHostnames=True,
          Tags=[{'Key':'Name','Value':'ebo-cloud-lab'}])
igw = add('InternetGateway','EC2::InternetGateway')
add('GatewayAttachment','EC2::VPCGatewayAttachment',VpcId=vpc,InternetGatewayId=igw)
route = add('RouteTable','EC2::RouteTable',VpcId=vpc)
add('InternetRoute','EC2::Route',RouteTableId=route,DestinationCidrBlock='0.0.0.0/0',GatewayId=igw)
R['InternetRoute']['DependsOn']='GatewayAttachment'
subnets=[]
for i in range(2):
    sn=add(f'Subnet{i}','EC2::Subnet',VpcId=vpc,CidrBlock=f'10.87.{i}.0/24',
           AvailabilityZone={'Fn::Select':[i,{'Fn::GetAZs':''}]})
    add(f'RouteAssociation{i}','EC2::SubnetRouteTableAssociation',SubnetId=sn,RouteTableId=route)
    subnets.append(sn)
sg=add('TaskSecurityGroup','EC2::SecurityGroup',VpcId=vpc,GroupDescription='EBO task: no inbound access',
       SecurityGroupEgress=[{'IpProtocol':'-1','CidrIp':'0.0.0.0/0','Description':'Enabot Agora OpenAI and AWS APIs'}])
efs_sg=add('StorageSecurityGroup','EC2::SecurityGroup',VpcId=vpc,GroupDescription='NFS only from EBO tasks',
           SecurityGroupIngress=[{'IpProtocol':'tcp','FromPort':2049,'ToPort':2049,'SourceSecurityGroupId':sg}])
fs=add('FileSystem','EFS::FileSystem',Encrypted=True,PerformanceMode='generalPurpose',ThroughputMode='bursting',
       BackupPolicy={'Status':'ENABLED'},
       FileSystemPolicy={'Version':'2012-10-17','Statement':[{'Effect':'Deny','Principal':'*','Action':'elasticfilesystem:*',
         'Resource':'*','Condition':{'Bool':{'aws:SecureTransport':'false'}}}]})
R['FileSystem'].update(DeletionPolicy='Retain',UpdateReplacePolicy='Retain')
for i,sn in enumerate(subnets):
    add(f'MountTarget{i}','EFS::MountTarget',FileSystemId=fs,SubnetId=sn,SecurityGroups=[efs_sg])
for name,folder in [('Engine','engine'),('Assistant','assistant')]:
    add(name+'AccessPoint','EFS::AccessPoint',FileSystemId=fs,PosixUser={'Uid':'1000','Gid':'1000'},
        RootDirectory={'Path':'/'+folder,'CreationInfo':{'OwnerUid':'1000','OwnerGid':'1000','Permissions':'0750'}})
    add(name+'Repository','ECR::Repository',RepositoryName='ebo-cloud/'+folder,ImageTagMutability='IMMUTABLE',
        ImageScanningConfiguration={'ScanOnPush':True},EmptyOnDelete=False)
    R[name+'Repository'].update(DeletionPolicy='Retain',UpdateReplacePolicy='Retain')
    add(name+'Secret','SecretsManager::Secret',Name='ebo-cloud/'+folder,Description='EBO cloud runtime configuration',
        SecretString='{"bootstrap_pending":true}')
    R[name+'Secret'].update(DeletionPolicy='Retain',UpdateReplacePolicy='Retain')
    add(name+'Log','Logs::LogGroup',LogGroupName='/ebo-cloud/'+folder,RetentionInDays=14)
    R[name+'Log']['DeletionPolicy']='Retain'
trust={'Version':'2012-10-17','Statement':[{'Effect':'Allow','Principal':{'Service':'ecs-tasks.amazonaws.com'},'Action':'sts:AssumeRole',
       'Condition':{'StringEquals':{'aws:SourceAccount':ref('AWS::AccountId')},'ArnLike':{'aws:SourceArn':sub('arn:${AWS::Partition}:ecs:${AWS::Region}:${AWS::AccountId}:*')}}}]}
add('ExecutionRole','IAM::Role',AssumeRolePolicyDocument=trust,
    Policies=[{'PolicyName':'ReadEboConfiguration','PolicyDocument':{'Version':'2012-10-17','Statement':[
      {'Effect':'Allow','Action':['ecr:GetAuthorizationToken'],'Resource':'*'},
      {'Effect':'Allow','Action':['ecr:BatchCheckLayerAvailability','ecr:GetDownloadUrlForLayer','ecr:BatchGetImage'],
       'Resource':[att('EngineRepository','Arn'),att('AssistantRepository','Arn')]},
      {'Effect':'Allow','Action':['logs:CreateLogStream','logs:PutLogEvents'],
       'Resource':[att('EngineLog','Arn'),att('AssistantLog','Arn')]},
      {'Effect':'Allow','Action':['secretsmanager:GetSecretValue'],'Resource':[ref('EngineSecret'),ref('AssistantSecret')]}]}}])
add('TaskRole','IAM::Role',AssumeRolePolicyDocument=trust,Policies=[{'PolicyName':'EboStorage',
    'PolicyDocument':{'Version':'2012-10-17','Statement':[
      {'Effect':'Allow','Action':['elasticfilesystem:ClientMount','elasticfilesystem:ClientWrite'],
       'Resource':att('FileSystem','Arn'),'Condition':{'StringEquals':{'elasticfilesystem:AccessPointArn':[att('EngineAccessPoint','Arn'),att('AssistantAccessPoint','Arn')]}}}]}}])
add('Cluster','ECS::Cluster',ClusterName='ebo-cloud-lab',ClusterSettings=[{'Name':'containerInsights','Value':'enhanced'}])
containers=[]
for name,service,cpu,mem,port in [('Engine','ebo-engine',512,1024,8098),('Assistant','realtime-assistant',512,768,8099)]:
    containers.append({'Name':service,'Image':ref(name+'Image'),'Essential':True,'Cpu':cpu,'MemoryReservation':mem,
      'LinuxParameters':{'InitProcessEnabled':True},'StopTimeout':60,
      'Environment':[{'Name':'EBO_CLOUD_SERVICE','Value':service},{'Name':'EBO_CLOUD_ENV','Value':'lab'},
                     {'Name':'PYTHONUNBUFFERED','Value':'1'},{'Name':'EBO_CLOUD','Value':'1'}],
      'Secrets':[{'Name':'EBO_BOOTSTRAP_JSON','ValueFrom':ref(name+'Secret')}],
      'MountPoints':[{'SourceVolume':name+'Data','ContainerPath':'/data','ReadOnly':False}],
      'HealthCheck':{'Command':['CMD','python','/cloud/healthcheck.py',service],'Interval':30,'Timeout':5,'Retries':3,'StartPeriod':120},
      'LogConfiguration':{'LogDriver':'awslogs','Options':{'awslogs-group':ref(name+'Log'),'awslogs-region':ref('AWS::Region'),
         'awslogs-stream-prefix':service,'mode':'non-blocking','max-buffer-size':'10m'}}})
add('TaskDefinition','ECS::TaskDefinition',Family='ebo-cloud-lab',Cpu='1024',Memory='2048',NetworkMode='awsvpc',
    RequiresCompatibilities=['FARGATE'],RuntimePlatform={'CpuArchitecture':'X86_64','OperatingSystemFamily':'LINUX'},
    ExecutionRoleArn=att('ExecutionRole','Arn'),TaskRoleArn=att('TaskRole','Arn'),ContainerDefinitions=containers,
    Volumes=[{'Name':name+'Data','EFSVolumeConfiguration':{'FilesystemId':fs,'TransitEncryption':'ENABLED',
              'AuthorizationConfig':{'AccessPointId':ref(name+'AccessPoint'),'IAM':'ENABLED'}}} for name in ['Engine','Assistant']])
add('Service','ECS::Service',ServiceName='ebo-cloud-lab',Cluster=ref('Cluster'),TaskDefinition=ref('TaskDefinition'),
    DesiredCount=ref('DesiredCount'),LaunchType='FARGATE',PlatformVersion='1.4.0',EnableExecuteCommand=False,
    DeploymentConfiguration={'MinimumHealthyPercent':0,'MaximumPercent':100,'DeploymentCircuitBreaker':{'Enable':True,'Rollback':True}},
    NetworkConfiguration={'AwsvpcConfiguration':{'AssignPublicIp':'ENABLED','Subnets':subnets,'SecurityGroups':[sg]}})
R['Service']['DependsOn']=['MountTarget0','MountTarget1','InternetRoute']
template={'AWSTemplateFormatVersion':'2010-09-09','Description':'EBO Fargate lab: two containers, private APIs, persistent storage, telemetry',
 'Parameters':{'EngineImage':{'Type':'String'},'AssistantImage':{'Type':'String'},'DesiredCount':{'Type':'Number','Default':0,'AllowedValues':[0,1]}},
 'Resources':R,'Outputs':{name:{'Value':value} for name,value in {
   'Cluster':ref('Cluster'),'Service':att('Service','Name'),'TaskDefinition':ref('TaskDefinition'),
   'EngineRepository':att('EngineRepository','RepositoryUri'),'AssistantRepository':att('AssistantRepository','RepositoryUri'),
   'EngineSecret':ref('EngineSecret'),'AssistantSecret':ref('AssistantSecret'),'FileSystem':fs}.items()}}
if __name__=='__main__':
    Path(__file__).with_name('fargate.template.json').write_text(json.dumps(template,indent=2),encoding='utf-8')
    print('Generated cloud/fargate.template.json')
