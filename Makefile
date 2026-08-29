.PHONY: test test-regression test-extended test-all test-evals secrets-gen-env secrets-verify proxy-restart proxy-status help

# ── Regression tests (run after every change) ──────────────────────────────

test-regression:
	@echo "Running fast regression suite (33 tests)..."
	python3 -m pytest tests/test_regression_suite.py -v --tb=short

test-extended:
	@echo "Running extended regression suite (62 tests)..."
	python3 -m pytest tests/test_regression_extended.py -v --tb=short


# ── Infrastructure evals (run before push) ───────────────────────────────

test-evals:
	@echo "Running infrastructure evals (5 suites, ~67s)..."
	python3 -m pytest tests/test_eval_provider_fallback.py tests/test_eval_memory_recall.py tests/test_eval_doc_drift.py tests/test_eval_backup_drill.py tests/test_eval_container_memory.py -v --tb=short

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
	# ── Linting and type checking ──────────────────────────────────────────

lint:
	@echo "Running ruff linter..."
	ruff check --config .ruff.toml

lint-fix:
	@echo "Running ruff with auto-fix..."
	ruff check --config .ruff.toml --fix

typecheck:
	@echo "Running mypy type checker..."
	mypy --config-file mypy.ini core/ --ignore-missing-imports

# ── Security scanning ──────────────────────────────────────────────────

scan-images:
	@echo "Scanning container images for CVEs..."
	@for img in 3627171(docker ps --format "{{.Image}}" | sort -u | head -10); do 		echo "Scanning \$\..."; 		trivy image --severity HIGH,CRITICAL --quiet "\$\" 2>&1 | grep -E "^Total:" || true; 	done

secrets-check:
	@echo "Checking secret rotation..."
	python3 /home/rohit/.hermes/scripts/secret_rotation_check.py

# ── Backup and restore ─────────────────────────────────────────────────

backup-drill:
	@echo "Running backup restore drill..."
	bash /home/rohit/.hermes/scripts/backup_restore_test.sh

# ── Documentation ──────────────────────────────────────────────────────

sync-docs:
	@echo "Syncing documentation..."
	python3 /home/rohit/.hermes/scripts/claude_md_sync.py --all --quiet

drift-check:
	@echo "Checking for document drift..."
	python3 /home/rohit/.hermes/scripts/doc_drift_check.py --json --quiet

# ── Grafana alerts ─────────────────────────────────────────────────────

grafana-alerts:
	@echo "Setting up Grafana alerts..."
	python3 /home/rohit/.hermes/scripts/grafana_alert_setup.py

@echo "test              Run all regression tests (mandatory before push)"
	@echo "test-regression   Fast suite — proxy + secrets (33 tests, ~35s)"
	@echo "test-extended     Extended — MCP + cron + n8n + backups + memory (62 tests, <1s)"
	@echo "test-all          Full unit test suite (52 tests)"
		@echo "lint              Run ruff linter"
	@echo "lint-fix          Run ruff with auto-fix"
	@echo "typecheck         Run mypy type checker"
	@echo "scan-images       Scan container images for CVEs (Trivy)"
	@echo "secrets-check     Check secret rotation status"
	@echo "backup-drill      Run automated backup restore test"
	@echo "sync-docs         Sync documentation"
	@echo "drift-check       Check for document drift"
	@echo "grafana-alerts    Setup Grafana alerting rules"
	@echo "secrets-gen-env   Regenerate .env from Vaultwarden"
	@echo "secrets-verify    Verify all API keys present"
	@echo "proxy-restart     Restart proxy"
	@echo "proxy-status      Show provider status"
