"""Cloud supervisor with JSON health and explicitly selected business events."""
import datetime
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from urllib.request import Request, urlopen

SERVICE = os.environ.get('EBO_CLOUD_SERVICE', 'unknown')
BOOT_ID = str(uuid.uuid4())
LOCK = threading.Lock()
REDACTIONS = []

def protect(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if any(word in key.lower() for word in ('key','token','password','email','instructions','prompt')) and isinstance(item,str) and item:
                REDACTIONS.append(item)
            elif isinstance(item, (dict,list)):
                protect(item)
    elif isinstance(value,list):
        for item in value: protect(item)

def redact(message):
    message = str(message)
    for secret in sorted(REDACTIONS, key=len, reverse=True):
        message = message.replace(secret, '[REDACTED]')
    message = re.sub(r'(?i)(bearer\s+)[\w.\-]+', r'\1[REDACTED]', message)
    message = re.sub(r'\bsk-[A-Za-z0-9_-]+', '[REDACTED]', message)
    return message

def emit(event, severity='INFO', **fields):
    record = {'schema_version':1,'timestamp':datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'service':SERVICE,'environment':'lab','boot_id':BOOT_ID,'event':event,'severity':severity,**fields}
    with LOCK:
        print(json.dumps(record, ensure_ascii=False, separators=(',',':')),flush=True)


BUSINESS_EVENTS = {
    'conversation.user.transcript', 'conversation.assistant.output',
    'conversation.transcription.failed', 'speaker.stream.failed',
    'speaker.playback.result', 'speaker.interrupted', 'storage.write_failed',
    'realtime.connected', 'realtime.disconnected', 'realtime.server_error',
    'realtime.response.done',
}
BUSINESS_FIELDS = {
    'event_id', 'source_timestamp', 'received_at', 'received_at_local',
    'session_started_at', 'item_id', 'response_id', 'output_id', 'transcript',
    'transcript_chars', 'chunk_index', 'chunk_count', 'languages', 'persisted',
    'file_path', 'audio_file', 'text_file', 'streamed', 'interrupted',
    'generated_ms', 'played_ms', 'stream_id', 'status', 'error_type', 'error_code',
    'artifact', 'model', 'reason', 'close_code', 'planned', 'speech_ms',
    'input_tokens', 'output_tokens', 'total_tokens',
}


def redact_value(value):
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [redact_value(v) for v in value]
    if isinstance(value, dict):
        return {k: redact_value(v) for k, v in value.items()}
    return value


def forward_line(line):
    line = line.rstrip()
    if line.startswith('EBO_EVENT_V1 '):
        try:
            row = json.loads(line[len('EBO_EVENT_V1 '):])
        except json.JSONDecodeError:
            row = None
        if isinstance(row, dict) and row.get('event') in BUSINESS_EVENTS:
            severity = row.get('severity', 'INFO')
            if severity not in {'INFO', 'WARNING', 'ERROR'}:
                severity = 'INFO'
            # Child data cannot replace service, boot_id, timestamp or EMF metrics.
            fields = {k: redact_value(v) for k, v in row.items() if k in BUSINESS_FIELDS}
            emit(row['event'], severity, **fields)
            return
        emit('application.event_rejected', 'WARNING')
        return
    line = redact(line)
    if line:
        emit('application.log', severity='ERROR' if re.search(r'\b(ERROR|Traceback|FATAL)\b',line)
             else 'WARNING' if re.search(r'\b(WARN|WARNING)\b',line) else 'INFO', message=line[:16000])

def bootstrap(data_dir='/data'):
    payload = json.loads(os.environ.pop('EBO_BOOTSTRAP_JSON'))
    if payload.get('bootstrap_pending') or not isinstance(payload.get('environment'),dict):
        raise ValueError('Configuration has not been provisioned')
    protect(payload)
    os.environ.update({k:str(v) for k,v in payload['environment'].items()})
    os.environ.update(EBO_CLOUD='1', EBO_CLOUD_SERVICE=SERVICE, PYTHONUNBUFFERED='1')
    data_dir = Path(data_dir)
    data_dir.mkdir(exist_ok=True)
    if SERVICE == 'ebo-engine':
        options = payload['options']
        if not all(options.get(k) for k in ('email','password','payload_key','sign_key')):
            raise ValueError('Engine account configuration incomplete')
        options.update(mcp=False, expose_mqtt=False)
        target = data_dir / 'options.json'
        temp = target.with_suffix('.tmp')
        temp.write_text(json.dumps(options),encoding='utf-8')
        temp.chmod(0o600)
        temp.replace(target)
        choices = data_dir / 'ui_choices.json'
        if not choices.exists():
            if 'ui_choices' not in payload:
                raise ValueError('Explicit initial microphone privacy state is required')
            choices.write_text(json.dumps(payload['ui_choices']),encoding='utf-8')
            choices.chmod(0o600)
        os.environ.update(EBO_PANEL_PORT='8101',EBO_API_PORT='8098',EBO_API_HOST='127.0.0.1')
    else:
        os.environ.update(EBO_RTSP_URL='rtsp://127.0.0.1:8554/ebo',EBO_API_URL='http://127.0.0.1:8098',
                          EBO_TALK_STREAM_URL='ws://127.0.0.1:8200/talk',
                          EBO_ASSISTANT_AUDIO_URL='http://127.0.0.1:8099/audio',EBO_ASSISTANT_PORT='8099')

SAFE_HEALTH = {'ok','uptime_seconds','realtime_connected','video_streaming','audio_streaming','source_audio_ok',
 'source_audio_status','media_ok','media_starting','last_frame_age_seconds','last_audio_age_seconds',
 'speaker_stream_status','speaker_stream_id','speaker_stream_played_ms','speaker_stream_failures','speaker_stream_fallbacks',
 'barge_in_enabled','barge_in_count','unplanned_realtime_reconnects','realtime_session_rollovers',
 'user_transcripts_received','assistant_outputs_persisted','visual_context_items_added','media_recovery_attempts'}

def monitor(stop):
    while not stop.wait(15):
        try:
            port,path = (8098,'/api/robots') if SERVICE=='ebo-engine' else (8099,'/health')
            req = Request(f'http://127.0.0.1:{port}{path}',headers={'X-Enabot-Token':os.environ.get('EBO_API_TOKEN','')})
            with urlopen(req,timeout=3) as response:
                payload=json.load(response)
            if SERVICE=='realtime-assistant':
                health={k:v for k,v in payload.items() if k in SAFE_HEALTH}
                metrics={key:float(bool(payload.get(source))) for key,source in [('BusinessReady','ok'),('RealtimeConnected','realtime_connected'),('SourceAudioOk','source_audio_ok')]}
            else:
                robots=payload.get('robots',[]) if isinstance(payload,dict) else payload
                if isinstance(robots,dict): robots=list(robots.values())
                health={'robot_count':len(robots),'audio_sources':[{
                    k:r.get('audio_health',{}).get(k) for k in ('status','source_audio_ok','last_packet_at','last_pcm_at')
                } for r in robots if isinstance(r,dict)]}
                metrics={'ApiReachable':1}
            emf={'Timestamp':int(time.time()*1000),'CloudWatchMetrics':[{'Namespace':'EBO/Diagnostics','Dimensions':[['service','environment']],
                 'Metrics':[{'Name':key,'Unit':'Count'} for key in metrics]}]}
            emit('health.snapshot',health=health,_aws=emf,**metrics)
        except Exception as exc:
            emit('health.poll_failed','WARNING',error_type=type(exc).__name__)

def main():
    bootstrap()
    command=['/app/run.sh'] if SERVICE=='ebo-engine' else [sys.executable,'-u','/app/app.py']
    child=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,start_new_session=True,
                           text=True,encoding='utf-8',errors='replace',bufsize=1)
    stop=threading.Event()
    def forward(signum,frame):
        stop.set()
        if child.poll() is None:
            try: os.killpg(child.pid,signum)
            except ProcessLookupError: pass
    signal.signal(signal.SIGTERM,forward)
    signal.signal(signal.SIGINT,forward)
    threading.Thread(target=monitor,args=(stop,),daemon=True).start()
    emit('process.started',child_pid=child.pid)
    try:
        for line in child.stdout:
            forward_line(line)
        code=child.wait()
        emit('process.exited','INFO' if code==0 else 'ERROR',exit_code=code)
        return code if code>=0 else 128-code
    finally:
        stop.set()
        if child.poll() is None:
            forward(signal.SIGTERM,None)
            try: child.wait(timeout=15)
            except subprocess.TimeoutExpired: os.killpg(child.pid,signal.SIGKILL)

if __name__=='__main__':
    try: sys.exit(main())
    except Exception as exc:
        emit('bootstrap.failed','ERROR',error_type=type(exc).__name__)
        sys.exit(1)
