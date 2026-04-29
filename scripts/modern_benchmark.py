import time
import json
import requests
import sys

PROXY_URL = "http://localhost:8080/v1/chat/completions"

def benchmark(prompt="Explain why a homelab needs a local LLM in 50 words."):
    print(f"🚀 Starting Benchmark (Non-Streaming)...")
    print(f"Endpoint: {PROXY_URL}")
    print(f"Prompt: {prompt}\n")
    
    payload = {
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 200,
        "stream": False
    }
    
    start_time = time.time()
    
    try:
        response = requests.post(PROXY_URL, json=payload, timeout=300)
        response.raise_for_status()
        data = response.json()
        
        total_time = time.time() - start_time
        full_content = data["choices"][0]["message"]["content"]
        
        # Estimate token count (chars / 4) if usage not present
        usage = data.get("usage", {})
        token_count = usage.get("total_tokens", len(full_content) // 4)
        
        tps = token_count / total_time if total_time > 0 else 0
        
        # Extract provider
        provider = "Unknown"
        if "— via" in full_content:
            provider = full_content.split("— via")[-1].strip()
        
        print(full_content)
        print("\n" + "="*50)
        print(f"📊 RESULTS:")
        print(f"Provider:   {provider}")
        print(f"Total Time: {total_time:.2f}s")
        print(f"Tokens:     {token_count}")
        print(f"TPS:        {tps:.2f} tokens/sec")
        print("="*50)
        
    except Exception as e:
        print(f"\n❌ Benchmark FAILED: {e}")

if __name__ == "__main__":
    benchmark()
