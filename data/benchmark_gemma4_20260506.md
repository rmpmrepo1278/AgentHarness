# Gemma 4 26B Benchmark Results — 2026-05-06

## System
- CPU: 8 threads
- RAM: 36 GB total, ~27 GB available during tests
- ik-llama.cpp: v4465 (b9372190), upgraded from v4437 (3a945af4)

## Model
- Gemma 4 26B-A4B-it Q4_K_M (bartowski/google_gemma-4-26B-A4B-it-GGUF)
- Size: 16.4 GB
- Params: 25.97B (MoE, 128 experts, 8 active per token)
- Context: 4096

## Results

| Configuration | TPS | Speedup |
|---------------|-----|---------|
| Baseline (no MTP) | 3.47 | 1.0× |
| `-mtp` flag | 4.13 | 1.19× |
| Custom drafter GGUF | — | Failed (magic bytes + arch not supported) |

## Key Findings

1. `-mtp` flag works on v4465 even without dedicated MTP heads in weights — 19% speedup
2. Custom drafter (SeatownSin/gemma-4-E4B-mtp-drafter) cannot be loaded because:
   - Hand-rolled GGUF had magic bytes reversed (FUGG vs GGUF)
   - Even with correct GGUF, the custom 4-layer Q-only architecture isn't recognized
   - Would require adding a new LLM_ARCH to llama.cpp source
3. Gemma 4 E4B (dense, ~4B params) was considered but rejected — similar active params to 26B-A4B MoE, estimated only 1.5-2× speedup, lower quality

## Recommendations

- Use `-mtp` flag for Gemma 4 26B (4.13 TPS)
- Keep Qwen3.5-9B as primary fast model
- Use Gemma 4 26B for tasks needing higher quality
- Custom drafter support would require C++ changes to llama.cpp

## Files Created
- `/home/rohit/models/google_gemma-4-26B-A4B-it-Q4_K_M.gguf` (16 GB)
- `/home/rohit/models/gemma-4-mtp-drafter.safetensors` (298 MB)
- `/home/rohit/models/gemma-4-mtp-drafter.gguf` (149 MB, non-functional)
- `/home/rohit/models/convert_drafter_to_gguf.py` (conversion script)
- `/home/rohit/models/benchmark_gemma_mtp.sh` (benchmark script)
