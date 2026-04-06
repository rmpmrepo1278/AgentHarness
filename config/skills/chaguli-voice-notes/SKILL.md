---
name: chaguli-voice-notes
description: Process Telegram voice messages — transcribe, extract intents, and execute actions (reminders, searches, commands, notes)
requires:
  binaries: ["curl", "python3"]
---

# Voice Note Processing

When the user sends a voice message via Telegram, OpenClaw receives it as an audio file. This skill teaches you how to transcribe it and act on the content.

## Step 1: Transcribe the Voice Note

OpenClaw may auto-transcribe voice messages depending on your configuration. If you receive a transcription, proceed to Step 2.

If you receive a raw audio file path, transcribe it:

### Option A: Use Groq Whisper API (fast, uses API quota)

```bash
curl -sf https://api.groq.com/openai/v1/audio/transcriptions \
  -H "Authorization: Bearer ${GROQ_API_KEY}" \
  -F "file=@AUDIO_FILE_PATH" \
  -F "model=whisper-large-v3-turbo" \
  -F "response_format=text" \
  -F "language=en"
```

### Option B: Use local Whisper via llama.cpp (free, slower)

```bash
# If whisper.cpp is installed
/opt/whisper.cpp/main -m /opt/models/whisper/ggml-base.en.bin -f AUDIO_FILE_PATH --no-timestamps -otxt
cat AUDIO_FILE_PATH.txt
```

### Option C: Use Python whisper (free, moderate speed)

```bash
python3 -c "
import whisper
model = whisper.load_model('base')
result = model.transcribe('AUDIO_FILE_PATH')
print(result['text'])
"
```

## Step 2: Parse Intent from Transcription

Once you have the text, identify what the user wants. Common patterns:

### Reminders / Tasks
Triggers: "remind me", "don't forget", "remember to", "add a task", "todo"

```bash
# Extract the reminder content and save
bash /opt/agentharness/scripts/chaguli_memory.sh add tasks "EXTRACTED_REMINDER" interaction
```

Respond: "Got it. I'll remind you: [reminder text]"

### Commands / Actions
Triggers: "restart", "check", "deploy", "download", "search", "clean up"

Execute the relevant command using your other skills. For example:
- "restart jellyfin" → `docker restart jellyfin`
- "download that movie" → use arr stack skill
- "check the system" → use dashboard skill
- "deploy this repo" → `bash /opt/agentharness/scripts/github_deploy.sh URL`

### Questions / Research
Triggers: "what is", "how do I", "why is", "find", "look up"

Use SearXNG to search:
```bash
SEARXNG_URL="${SEARXNG_URL:-http://localhost:8888}"
curl -sf "${SEARXNG_URL}/search?q=QUERY&format=json" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for r in data.get('results', [])[:3]:
    print(f'• {r.get(\"title\", \"\")} — {r.get(\"url\", \"\")}')
"
```

### Notes / Knowledge Capture
Triggers: "save this", "note that", "remember that", "I learned", "for later"

```bash
bash /opt/agentharness/scripts/chaguli_memory.sh add knowledge "CONTENT" interaction
```

### Multiple Intents
Voice notes often contain multiple requests:
> "Remind me to check the backup drive tomorrow AND also download that movie we talked about"

Parse each intent separately and execute them in order. Respond with confirmation of each action.

## Step 3: Respond

Keep responses concise for Telegram:
- Confirm what you heard (brief paraphrase)
- Confirm what you did
- If uncertain about any part, ask for clarification

Example:
> User: *voice note* "Hey check if Jellyfin is running and also remind me to update the arr stack configs this weekend"
> Chaguli: "Jellyfin is running (up 3 days, healthy). Reminder saved: update arr stack configs this weekend."

## Handling Transcription Errors

Voice-to-text is imperfect. Common issues:
- "open claw" might mean "OpenClaw"
- Technical terms may be garbled
- Numbers and IPs may be misheard

When in doubt, confirm: "I heard 'restart jelly fin' — did you mean restart the Jellyfin container?"

## Audio Quality Tips

If transcription is consistently poor, suggest:
- Speak closer to the phone mic
- Reduce background noise
- Use text for technical terms (IPs, container names, URLs)
