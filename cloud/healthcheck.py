"""Liveness only. Upstream readiness belongs in business telemetry."""
import json
import os
import socket
import sys
from urllib.request import Request, urlopen

service=sys.argv[1]
if service=='ebo-engine':
    # The healthcheck process does not inherit the entrypoint's secret environment.
    with open('/data/api_token',encoding='utf-8') as source: token=source.read().strip()
    req=Request('http://127.0.0.1:8098/api/robots',headers={'X-Enabot-Token':token})
    with urlopen(req,timeout=2) as response: json.load(response)
    with socket.create_connection(('127.0.0.1',1883),timeout=2): pass
else:
    with urlopen('http://127.0.0.1:8099/live',timeout=2) as response:
        if not json.load(response).get('ok'): sys.exit(1)
