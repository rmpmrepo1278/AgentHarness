#!/usr/bin/env python3
import json, sqlite3, subprocess, sys, re
from datetime import datetime

NPM_DB = "/var/lib/docker/volumes/npm_npm-data/_data/database.sqlite"
CERT_HOME, CERT_DUCKDNS, USER_ID = 6, 2, 1
SKIP = {'autoheal','watchtower','docker-socket-proxy','docker-mcp','backup-mcp','file-mcp','git-mcp','network-mcp','doctor-mcp','rss-mcp','global-chat-mcp','homelab-exec','homelab-ops-mcp','mcp-gateway','paperless-mcp','hermes-memory-mcp','bookstack-db','linkwarden-db','paperless-db','immich_database','redis'}
OVERRIDES = {
    'calibre-web': 'books',
    'immich_server': 'immich',
    'hermes-webui-hermes-webui-1': 'hermes',
    'nginx-proxy-manager': 'npm-admin',
    'pihole': 'pihole',
    'healthchecks': 'healthchecks',
    
    'freellmapi': 'free-llm',
    'agent-status-api': 'agent-status',
}
WS = {'homeassistant','hermes-webui-hermes-webui-1'}
HOME_DOM = "home"
DUCK_DOM = "chagulihome.duckdns.org"

def log(m): print(f"[npm_sync] {m}")

def get_containers():
    r = subprocess.run(["docker","ps","--format",'{{.Names}}\t{{.Ports}}'], capture_output=True, text=True, timeout=15)
    c = {}
    for line in r.stdout.strip().split('\n'):
        if not line or '\t' not in line: continue
        n, p = line.split('\t', 1)
        if p.strip(): c[n] = p.strip()
    return c

def parse_port(s):
    for part in s.split(','):
        part = part.strip()
        m = re.match(r'(?:0\.0\.0\.0|127\.0\.0\.1|\[\:\:]):(\d+)->\d+', part)
        if m: return int(m.group(1))
    return None

def parse_domains(val):
    """Parse domain_names field - handles both proper JSON and unquoted arrays."""
    s = val.strip()
    if s.startswith('[') and s.endswith(']'):
        inner = s[1:-1]
        if not inner.strip():
            return []
        parts = re.findall(r'"([^"]*)"|\'([^\']*)\'|([^,\s]+)', inner)
        result = []
        for p in parts:
            d = p[0] or p[1] or p[2]
            if d:
                result.append(d.strip())
        return result
    try:
        return json.loads(s)
    except:
        return [s]

def existing(c):
    c.execute("SELECT domain_names FROM proxy_host WHERE is_deleted=0")
    d = set()
    for row in c.fetchall():
        for x in parse_domains(row[0]):
            d.add(x.lower())
    return d

def create(c, domain, host, port, ws=False, duck=False):
    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    cid = CERT_DUCKDNS if duck else CERT_HOME
    c.execute("""INSERT INTO proxy_host (created_on,modified_on,owner_user_id,is_deleted,domain_names,forward_host,forward_port,access_list_id,certificate_id,ssl_forced,caching_enabled,block_exploits,advanced_config,meta,allow_websocket_upgrade,http2_support,forward_scheme,enabled,locations,hsts_enabled,hsts_subdomains,trust_forwarded_proto) VALUES (?,?,?,0,?,?,?,0,?,?,0,1,'',?,?,0,'http',1,?,?,0,0)""",
        (now,now,USER_ID,json.dumps([domain]),host,port,cid,1,json.dumps({"letsencrypt_agree":False,"dns_challenge":False,"nginx_online":True,"nginx_err":None}),1 if ws else 0,json.dumps([]),1 if duck else 0))
    return c.lastrowid

def reload():
    try:
        subprocess.run(["docker","exec","nginx-proxy-manager","nginx","-s","reload"], capture_output=True, text=True, timeout=10)
        log("nginx reloaded")
    except subprocess.TimeoutExpired:
        log("nginx reload timed out")
    except subprocess.CalledProcessError as e:
        log(f"nginx reload failed: {e.stderr.strip() if e.stderr else e}")
    except FileNotFoundError:
        log("docker not found, skipping nginx reload")

def main():
    log("Scanning...")
    containers = get_containers()
    log(f"Found {len(containers)} containers")
    conn = sqlite3.connect(NPM_DB)
    cur = conn.cursor()
    existing_d = existing(cur)
    log(f"Existing domains: {len(existing_d)}")
    created = []
    for name, ports in sorted(containers.items()):
        if name in SKIP: continue
        port = parse_port(ports)
        if port is None: continue
        sub = OVERRIDES.get(name, name)
        hd = f"{sub}.{HOME_DOM}"
        dd = f"{sub}.{DUCK_DOM}"
        ws_flag = name in WS
        need_h = hd not in existing_d
        need_d = dd not in existing_d
        if not need_h and not need_d: continue
        log(f"  {name} -> :{port}")
        if need_h:
            pid = create(cur, hd, "127.0.0.1", port, ws_flag, False)
            existing_d.add(hd); created.append(hd)
            log(f"    + {hd} (id={pid})")
        if need_d:
            pid = create(cur, dd, "127.0.0.1", port, ws_flag, True)
            existing_d.add(dd); created.append(dd)
            log(f"    + {dd} (id={pid})")
    if created:
        conn.commit()
        log(f"Created {len(created)}: {', '.join(created)}")
        reload()
    else:
        log("No new proxy hosts needed")
    conn.close()
    return 0

if __name__ == "__main__":
    sys.exit(main())
