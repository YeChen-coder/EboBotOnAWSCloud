"""Run inside --network none. Verify actual private configuration without upstream calls."""
import json
import os
import subprocess
import time
from urllib.request import urlopen

os.environ['EBO_BOOTSTRAP_JSON']=open('/config.json',encoding='utf-8').read()
os.environ['EBO_CLOUD_SERVICE']='realtime-assistant'
os.makedirs('/data',exist_ok=True)
with open('/tmp/smoke-output.jsonl','w+') as output:
    process=subprocess.Popen(['python','-u','/cloud/runtime.py'],stdout=output,stderr=subprocess.STDOUT)
    try:
        for _ in range(20):
            if process.poll() is not None: raise RuntimeError('Assistant terminated during startup')
            try:
                with urlopen('http://127.0.0.1:8099/live',timeout=1) as response: live=json.load(response)
                with urlopen('http://127.0.0.1:8099/health',timeout=1) as response: health=json.load(response)
                break
            except OSError: time.sleep(.5)
        else: raise RuntimeError('Liveness endpoint unavailable')
        assert live['ok'] is True
        assert health['ok'] is False
        time.sleep(1)
        assert process.poll() is None
    finally:
        process.terminate()
        try: process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait(); raise RuntimeError('Graceful shutdown timed out')
    output.seek(0)
    rows=[json.loads(line) for line in output if line.strip()]
    assert rows and all(row.get('schema_version')==1 for row in rows)
    print(json.dumps({'configuration_valid':True,'liveness_without_upstream':True,
                      'business_readiness_without_upstream':False,'graceful_shutdown_exit':process.returncode,
                      'structured_log_events':len(rows)}))
