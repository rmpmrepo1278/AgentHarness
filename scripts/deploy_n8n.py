import json, requests, sys

N8N_URL = 'http://localhost:5678/api/v1/workflows'
HEADERS = {'X-N8N-API-KEY': 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJjOTI5MDAxNi1kNmNiLTQ1ZDAtYmVkMi00ODFmMTZkOTg3OGQiLCJpc3MiOiJuOG4iLCJhdWQiOiJwdWJsaWMtYXBpIiwianRpIjoiYTYzMWVhZDMtZDI1ZC00YjQ5LTg1OGMtNDM5YjVjMmI3NGI5IiwiaWF0IjoxNzc1MjM5MDM0fQ.wJF59IqQfFnvaIB98m22Vo7UJM_INg-qqZuhwuNOeioe'}

workflow = {
    'name': 'Chief of Staff: Daily Strategic Briefing',
    'nodes': [
        {
            'parameters': {'rule': {'interval': [{'field': 'hours', 'hour': 7}]}},
            'id': '1', 'name': 'Morning Trigger', 'type': 'n8n-nodes-base.scheduleTrigger', 'typeVersion': 1, 'position': [100, 300]
        },
        {
            'parameters': {'command': 'python3 /home/rohit/.hermes/hermes-agent/scripts/cos_briefing.py'},
            'id': '2', 'name': 'Generate Briefing', 'type': 'n8n-nodes-base.executeCommand', 'typeVersion': 1, 'position': [300, 300]
        }
    ],
    'connections': {
        'Morning Trigger': {'main': [[{'node': 'Generate Briefing', 'type': 'main', 'index': 0}]]}
    },
    'active': False # Start inactive until user checks UI
}

resp = requests.post(N8N_URL, json=workflow, headers=HEADERS)
if resp.status_code == 200:
    print(f'SUCCESS: Workflow created with ID {resp.json().get("id")}')
else:
    print(f'FAILED: {resp.text}')
