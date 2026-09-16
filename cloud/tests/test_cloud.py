import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import runtime
import diagnostic
import make_template

class CloudBoundaryTests(unittest.TestCase):
    def test_structured_event_keeps_content_and_trusted_identity(self):
        row = {'event':'conversation.user.transcript','severity':'INFO',
               'transcript':'中文\n"ERROR" secret\\value','item_id':'i1',
               'service':'spoof','boot_id':'spoof','_aws':{'spoof':True},
               'api_key':'secret\\value'}
        with patch.object(runtime,'REDACTIONS',['secret\\value']), patch('sys.stdout',new_callable=io.StringIO) as out:
            runtime.forward_line('EBO_EVENT_V1 '+json.dumps(row,ensure_ascii=False))
            event=json.loads(out.getvalue())
        self.assertEqual(event['event'],'conversation.user.transcript')
        self.assertEqual(event['severity'],'INFO')
        self.assertEqual(event['transcript'],'中文\n"ERROR" [REDACTED]')
        self.assertEqual(event['service'],runtime.SERVICE)
        self.assertEqual(event['boot_id'],runtime.BOOT_ID)
        self.assertNotIn('_aws',event)
        self.assertNotIn('api_key',event)

    def test_malformed_structured_event_is_not_dumped(self):
        with patch('sys.stdout',new_callable=io.StringIO) as out:
            runtime.forward_line('EBO_EVENT_V1 {secret invalid JSON')
        self.assertEqual(json.loads(out.getvalue())['event'],'application.event_rejected')
        self.assertNotIn('secret',out.getvalue())

    def payload(self):
        return {'environment':{'EBO_API_TOKEN':'private-token-for-test'},
                'options':{'email':'private@example.test','password':'secret-pass',
                           'payload_key':'private-key-one','sign_key':'private-key-two','region':'CN'},
                'ui_choices':{'microphone':False}}

    def test_privacy_state_survives_config_refresh(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ,{},clear=True), patch.object(runtime,'SERVICE','ebo-engine'):
            payload=self.payload()
            os.environ['EBO_BOOTSTRAP_JSON']=json.dumps(payload)
            runtime.bootstrap(directory)
            choices=Path(directory)/'ui_choices.json'
            choices.write_text('{"microphone":false,"user_changed":true}')
            payload['ui_choices']={'microphone':True}
            os.environ['EBO_BOOTSTRAP_JSON']=json.dumps(payload)
            runtime.bootstrap(directory)
            self.assertEqual(json.loads(choices.read_text()),{'microphone':False,'user_changed':True})
            self.assertEqual(json.loads((Path(directory)/'options.json').read_text())['region'],'CN')
            self.assertEqual(os.environ['EBO_PANEL_PORT'],'8101')
            self.assertNotIn('EBO_BOOTSTRAP_JSON',os.environ)

    def test_missing_initial_privacy_state_rejected(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ,{},clear=True), patch.object(runtime,'SERVICE','ebo-engine'):
            payload=self.payload(); del payload['ui_choices']
            os.environ['EBO_BOOTSTRAP_JSON']=json.dumps(payload)
            with self.assertRaisesRegex(ValueError,'privacy'): runtime.bootstrap(directory)

    def test_cloud_urls_override_compose_and_preserve_tuning(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ,{},clear=True), patch.object(runtime,'SERVICE','realtime-assistant'):
            os.environ['EBO_BOOTSTRAP_JSON']=json.dumps({'environment':{'EBO_API_URL':'http://ebo-engine:8098','EBO_BARGE_IN_ENABLED':'false','MOTION_COOLDOWN_SECONDS':'3'}})
            runtime.bootstrap(directory)
            self.assertEqual(os.environ['EBO_API_URL'],'http://127.0.0.1:8098')
            self.assertEqual(os.environ['EBO_ASSISTANT_AUDIO_URL'],'http://127.0.0.1:8099/audio')
            self.assertEqual(os.environ['EBO_BARGE_IN_ENABLED'],'false')
            self.assertEqual(os.environ['MOTION_COOLDOWN_SECONDS'],'3')

    def test_secret_and_prompt_redaction(self):
        with patch.object(runtime,'REDACTIONS',[]):
            runtime.protect({'environment':{'OPENAI_API_KEY':'sk-private123','EBO_ASSISTANT_INSTRUCTIONS':'private family prompt'}})
            result=runtime.redact('sk-private123 private family prompt Bearer another-secret')
            self.assertNotIn('private',result)
            self.assertNotIn('another-secret',result)

    def test_changed_target_never_triggers_mutation(self):
        with patch.object(diagnostic,'aws',return_value={'taskArns':['new-task']}) as call:
            with self.assertRaisesRegex(RuntimeError,'Target changed'):
                diagnostic.replace_task('old-task','test')
            self.assertEqual(call.call_count,1)

    def test_no_overlapping_engines_or_public_ingress(self):
        resources=make_template.template['Resources']
        service=resources['Service']['Properties']
        self.assertEqual(service['DeploymentConfiguration']['MaximumPercent'],100)
        self.assertEqual(service['DeploymentConfiguration']['MinimumHealthyPercent'],0)
        self.assertFalse(service['EnableExecuteCommand'])
        self.assertNotIn('SecurityGroupIngress',resources['TaskSecurityGroup']['Properties'])
        self.assertEqual(len(resources['TaskDefinition']['Properties']['ContainerDefinitions']),2)

if __name__=='__main__': unittest.main()
