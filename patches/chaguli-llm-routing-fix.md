# Chaguli LLM Routing Fix: Route call_with_tools() Through Proxy

**Date:** 2026-04-10
**Target file:** `~/openclaw/chaguli/clients/llm_client.py`
**Problem:** `call_with_tools()` calls Groq directly. When Groq hits its daily cap, it falls back to local LLM (no tool support). The other 4 cloud providers (Google, Cerebras, SambaNova, OpenRouter) are never used for tool calls.
**Fix:** Route through the AgentHarness proxy at `http://192.168.29.10:8080` which handles provider rotation, budget tracking, and fallback automatically.

**Prerequisites:**
- The proxy server must be updated to support tool-calling passthrough (already done in `core/providers/proxy_server.py` in this commit).
- Restart the proxy after deploying: `sudo systemctl restart agentharness-llm-proxy`

---

## What to find

The `call_with_tools()` function (around line 589+) currently looks like this pattern:

```python
def call_with_tools(self, messages, tools, tool_choice="auto", model=None):
    """Call Groq with tool/function calling support."""
    if not self.groq_api_key:
        return self.call_local(messages[-1]["content"] if messages else "")

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {self.groq_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model or self.groq_model,
        "messages": messages,
        "tools": tools,
        "tool_choice": tool_choice,
        "max_tokens": 1024,
        "temperature": 0.1,
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        self._record_groq_call()
        return data["choices"][0]["message"]
    except Exception as e:
        logger.warning(f"Groq tool call failed: {e}")
        # Fallback to local without tools
        return self.call_local(messages[-1]["content"] if messages else "")
```

## What to replace it with

```python
def call_with_tools(self, messages, tools, tool_choice="auto", model=None):
    """Call LLM with tool/function calling support via proxy.

    Routes through AgentHarness proxy which handles provider rotation
    (Groq, Google, Cerebras, SambaNova, OpenRouter) and budget tracking.
    """
    proxy_url = LLAMA_URL + "/v1/chat/completions"

    payload = {
        "model": model or self.groq_model,
        "messages": messages,
        "tools": tools,
        "tool_choice": tool_choice,
        "max_tokens": 1024,
        "temperature": 0.1,
    }

    try:
        resp = requests.post(
            proxy_url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        self._record_proxy_call(data)
        return data["choices"][0]["message"]
    except Exception as e:
        logger.warning(f"Proxy tool call failed: {e}")
        # Fallback to local without tools
        return self.call_local(messages[-1]["content"] if messages else "")

def _record_proxy_call(self, data):
    """Record usage from a proxy tool call for local tracking."""
    try:
        timings = data.get("timings", {})
        usage = data.get("usage", {})
        provider = timings.get("provider", "unknown")
        tokens = usage.get("total_tokens", 0)
        logger.debug(
            f"Proxy tool call: provider={provider}, tokens={tokens}"
        )
        # If the proxy used Groq, still count against the local Groq
        # tracker so call_smart() knows the daily cap state
        if provider == "groq":
            self._record_groq_call()
    except Exception:
        pass
```

---

## Step-by-step: Apply on the homelab

### Option A: Python patch script

SCP this file to the homelab, then run the Python patcher:

```bash
scp patches/chaguli-proxy-routing-patcher.py rohit@192.168.29.10:~/
ssh rohit@192.168.29.10 'python3 ~/chaguli-proxy-routing-patcher.py'
```

The patcher script is at `patches/chaguli-proxy-routing-patcher.py` in this repo.

### Option B: Manual edit

1. SSH into the homelab:
   ```
   ssh rohit@192.168.29.10
   ```

2. Back up the file:
   ```
   cp ~/openclaw/chaguli/clients/llm_client.py ~/openclaw/chaguli/clients/llm_client.py.bak.$(date +%Y%m%d)
   ```

3. Open the file and find `def call_with_tools(` (around line 589).

4. Replace the entire function body with the replacement code above.

5. Add the new `_record_proxy_call` method right after `call_with_tools`.

6. Restart Chaguli:
   ```
   docker restart chaguli
   ```

7. Verify it works:
   ```
   docker logs chaguli --tail 50 -f
   ```
   Send a tool-using command via Telegram and watch for "Proxy tool call: provider=..." in the logs.

### Option C: Quick verification before applying

Test the proxy supports tool calls:
```bash
curl -s http://192.168.29.10:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role": "user", "content": "What time is it?"}],
    "tools": [{"type": "function", "function": {"name": "get_time", "description": "Get current time", "parameters": {"type": "object", "properties": {}}}}],
    "tool_choice": "auto"
  }' | python3 -m json.tool
```

If you get a valid response with `choices[0].message.tool_calls`, the proxy passthrough is working.

---

## Key design decisions

1. **No auth header needed** -- the proxy is on the local subnet (192.168.29.10) and manages API keys itself.
2. **Timeout increased to 60s** -- the proxy may try multiple providers before succeeding.
3. **`_record_proxy_call` tracks which provider was used** -- if Groq was selected by the proxy, we still update the local Groq counter so `call_smart()` cap tracking stays accurate.
4. **Signature unchanged** -- `call_with_tools(self, messages, tools, tool_choice="auto", model=None)` is identical, so all callers work without changes.
5. **The `groq_api_key` check is removed** -- the proxy handles provider availability. Even if no Groq key is set locally in Chaguli's env, the proxy has its own keys.
