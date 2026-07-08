#!/usr/bin/env python3
import subprocess, re, time, os

SERVICES_DIR = '/home/rohit/services/traefik/dynamic'
DOMAIN = 'chagulihome.duckdns.org'
SKIP = {'traefik', 'pihole', 'docker-socket-proxy', 'watchtower', 'autoheal', 'agent-status-api', 'hermes-webui-hermes-webui-1', 'healthchecks', 'redis', 'bookstack', 'grafana', 'netdata', 'cadvisor', 'homepage', 'calibre-web', 'paperless', 'immich_server', 'vaultwarden', 'portainer', 'qdrant'}

def get_containers():
    r = subprocess.run(['docker', 'ps', '--format', '{{.Names}}	{{.Ports}}'], capture_output=True, text=True, timeout=15)
    result = {}
    for line in r.stdout.strip().split('\n'):
        if '	' in line:
            parts = line.split('	')
            result[parts[0]] = parts[1] if len(parts) > 1 else ''
    return result

def parse_port(s):
    m = re.search(r'(?:0\.0\.0\.0|127\.0\.0\.1):(\d+)->\d+', s)
    return int(m.group(1)) if m else None

def get_ip(name):
    r = subprocess.run(['docker', 'inspect', name, '--format', '{{.NetworkSettings.Networks.traefik.IPAddress}}'], capture_output=True, text=True)
    return r.stdout.strip() or None

def ensure_net(name):
    subprocess.run(['docker', 'network', 'connect', 'traefik', name], capture_output=True)
    time.sleep(0.3)

def write_conf(name, ip, port):
    safe = name.replace('-', '').replace('_', '')
    domain = DOMAIN
    with open(SERVICES_DIR + '/' + safe + '.yml', 'w') as f:
        f.write(f"""http:
  routers:
    {safe}-rtr:
      rule: Host(`{safe}.{domain}`)
      entryPoints:
        - websecure
      service: {safe}-svc
      tls:
        certResolver: le
  services:
    {safe}-svc:
      loadBalancer:
        servers:
          - url: http://{ip}:{port}
""")
