import yaml

path = '/home/rohit/agentharness/docker-compose.mcp.yml'
with open(path, 'r') as f:
    data = yaml.safe_load(f)

# Standard limits for MCP services
limits = {
    'deploy': {
        'resources': {
            'limits': {
                'cpus': '0.50',
                'memory': '512M'
            },
            'reservations': {
                'memory': '128M'
            }
        }
    }
}

for service, config in data.get('services', {}).items():
    if 'deploy' not in config:
        config['deploy'] = limits['deploy']

with open(path, 'w') as f:
    yaml.dump(data, f, default_flow_style=False)
