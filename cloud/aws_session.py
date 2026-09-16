"""AWS CLI adapter. Credentials remain in the local AWS login cache."""
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_SETTINGS_PATH = ROOT / '.local/private-settings.json'

def _private_settings():
    try:
        value = json.loads(PRIVATE_SETTINGS_PATH.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

_SETTINGS = _private_settings()
REGION = os.environ.get('EBO_AWS_REGION', _SETTINGS.get('region', 'ca-central-1'))
ACCOUNT = os.environ.get('EBO_AWS_ACCOUNT_ID', _SETTINGS.get('account_id', '')).strip()
EFS_ID = os.environ.get('EBO_EFS_ID', _SETTINGS.get('efs_id', '')).strip()
EBO_APP_ID = os.environ.get('EBO_APP_ID', _SETTINGS.get('ebo_app_id', '')).strip()
SOURCE_ROOT = Path(os.environ.get(
    'EBO_SOURCE_ROOT',
    _SETTINGS.get('source_root', str(ROOT.parent / 'EBOBotRelated' / 'ebo-ai-home')),
))
PROFILE = "ebo-cloud"

def require_setting(environment_name, value):
    if not value:
        raise RuntimeError(
            f'Set {environment_name} or add it to private .local/private-settings.json'
        )
    return value

def environment():
    env = dict(os.environ)
    env.update(AWS_CONFIG_FILE=str(ROOT / '.local/aws-config'),
               AWS_SHARED_CREDENTIALS_FILE=str(ROOT / '.local/aws-credentials'),
               AWS_LOGIN_CACHE_DIRECTORY=str(ROOT / '.local/aws-login'), AWS_PAGER='',
               AWS_CLI_FILE_ENCODING='UTF-8', AWS_CLI_OUTPUT_ENCODING='UTF-8')
    return env

def aws(*args, input_text=None):
    cli = ROOT / '.tools/aws-cli/Amazon/AWSCLIV2/aws.exe'
    result = subprocess.run([str(cli), *args, '--profile', PROFILE, '--region', REGION,
                             '--output', 'json'], input=input_text, capture_output=True,
                            text=True, encoding='utf-8', env=environment())
    if result.returncode:
        # AWS errors can echo input values. Keep private raw diagnostics local.
        private = ROOT / '.local/aws-last-error.txt'
        private.write_text(result.stderr, encoding='utf-8')
        raise RuntimeError(f'AWS {args[0]} {args[1]} failed; inspect private .local/aws-last-error.txt')
    return json.loads(result.stdout) if result.stdout.strip() else {}

def verify_account():
    expected_account = require_setting('EBO_AWS_ACCOUNT_ID', ACCOUNT)
    identity = aws('sts', 'get-caller-identity')
    if identity['Account'] != expected_account:
        raise RuntimeError('Unexpected AWS account; refusing operation')
    return identity

def private_json(name, value):
    path = ROOT / '.local' / name
    path.parent.mkdir(parents=True, exist_ok=True)
    # ASCII JSON preserves Unicode values while avoiding Windows CLI locale decoding.
    path.write_text(json.dumps(value, ensure_ascii=True), encoding='utf-8')
    return 'file://' + path.as_posix()
