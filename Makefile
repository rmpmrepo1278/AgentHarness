.PHONY: test test-regression test-extended test-all secrets-gen-env secrets-verify proxy-restart proxy-status help

# ── Regression tests (run after every change) ──────────────────────────────

test-regression:
	@echo "Running fast regression suite (33 tests)..."
	python3 -m pytest tests/test_regression_suite.py -v --tb=short

test-extended:
	@echo "Running extended regression suite (62 tests)..."
	python3 -m pytest tests/test_regression_extended.py -v --tb=short

# ── Full combined run (95 proxy+infrastructure tests) ─────────────────────

test: test-regression test-extended
	@echo ""
	@echo "═══════════════════════════════════════════════"
	@echo "ALL REGRESSION TESTS PASSED — Safe to deploy"
	@echo "═══════════════════════════════════════════════"

# ── Full unit test suite (52 existing tests) ───────────────────────────────

test-all:
	@echo "Running full unit test suite..."
	python3 -m pytest tests/ -q --tb=line

# ── Secrets management ─────────────────────────────────────────────────────

secrets-gen-env:
	python3 /home/rohit/.secrets/vault.py gen-env

secrets-verify:
	python3 -c "import os;from pathlib import Path;env=Path('/home/rohit/agentharness/data/.env');assert env.exists(),'.env missing';c=env.read_text();r=['OPENROUTER_API_KEY','GROQ_API_KEY','CEREBRAS_API_KEY','SAMBANOVA_API_KEY','GOOGLE_FREE_API_KEY','LOCAL_LLM_URL'];m=[k for k in r if k not in c];assert not m,f'Missing: {m}';print('Verified OK')"

# ── Proxy control ──────────────────────────────────────────────────────────

proxy-restart:
	sudo systemctl restart agentharness-llm-proxy

proxy-status:
	curl -s http://localhost:8080/v1/status | python3 -m json.tool

# ── Help ───────────────────────────────────────────────────────────────────

help:
	@echo "test              Run all regression tests (mandatory before push)"
	@echo "test-regression   Fast suite — proxy + secrets (33 tests, ~35s)"
	@echo "test-extended     Extended — MCP + cron + n8n + backups + memory (62 tests, <1s)"
	@echo "test-all          Full unit test suite (52 tests)"
	@echo "secrets-gen-env   Regenerate .env from Vaultwarden"
	@echo "secrets-verify    Verify all API keys present"
	@echo "proxy-restart     Restart proxy"
	@echo "proxy-status      Show provider status"
