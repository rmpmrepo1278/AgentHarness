---
name: chaguli-readlater
description: Save articles, repos, links for later — weekly digest summarizes them
requires:
  binaries: ["curl", "python3"]
---

# Read It Later

When Rohit shares a link or says "save this", "bookmark this", "read later", or "interesting":

## Save a Link

```bash
python3 -c "
import json, os
from datetime import datetime

READLATER_FILE = '/opt/agentharness/readlater.json'

# Load existing
items = []
if os.path.exists(READLATER_FILE):
    items = json.load(open(READLATER_FILE))

items.append({
    'url': 'THE_URL',
    'title': 'TITLE_OR_DESCRIPTION',
    'tags': ['TAG1', 'TAG2'],
    'saved_at': datetime.now().isoformat(),
    'read': False,
    'summary': ''
})

json.dump(items, open(READLATER_FILE, 'w'), indent=2)
print(f'Saved. You have {len([i for i in items if not i[\"read\"]])} unread items.')
"
```

Tags to auto-assign based on content:
- URLs with github.com → `#repo`
- URLs with reddit.com → `#discussion`
- AI/LLM/model keywords → `#ai`
- Docker/homelab/self-hosted → `#homelab`
- General articles → `#article`

## Show Unread Items

```bash
python3 -c "
import json
items = json.load(open('/opt/agentharness/readlater.json'))
unread = [i for i in items if not i.get('read')]
print(f'{len(unread)} unread items:\n')
for i in unread[-10:]:
    tags = ' '.join(f'#{t}' for t in i.get('tags', []))
    print(f'• {i[\"title\"]} {tags}')
    print(f'  {i[\"url\"]}')
    print(f'  Saved: {i[\"saved_at\"][:10]}')
    print()
"
```

## Weekly Digest

During the weekly optimization, summarize unread items. Fetch each URL, summarize with the LLM, and send a digest:

> "This week you saved 5 links. Here's the TL;DR:
> 1. New Qwen model release — 15% faster on CPU. Relevant to your setup.
> 2. Reddit thread about arr stack automation — community uses X approach.
> ..."

## Mark as Read

```bash
python3 -c "
import json
items = json.load(open('/opt/agentharness/readlater.json'))
for i in items:
    if 'PARTIAL_URL_OR_TITLE' in i.get('url', '') or 'PARTIAL_URL_OR_TITLE' in i.get('title', ''):
        i['read'] = True
        print(f'Marked as read: {i[\"title\"]}')
json.dump(items, open('/opt/agentharness/readlater.json', 'w'), indent=2)
"
```
