import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core.providers.base import BudgetStatus, Complexity, LLMRequest, LLMResponse
from core.providers.router import Router


def _make_provider(name, tier="cloud", available=True, budget_ok=True):
    p = MagicMock()
    p.name = name
    p.tier = tier
    p.enabled = True
    p.is_available.return_value = available
    p.budget_status.return_value = BudgetStatus(estimated_remaining=100 if budget_ok else 0)
    p.complete.return_value = LLMResponse(
        text=f"from {name}", provider=name, model="mock",
        tokens_in=10, tokens_out=5, latency_ms=10.0, success=True
    )
    return p


class TestProviderRegistration:
    def test_tokenrouter_providers_registered(self):
        os.environ["TOKENROUTER_API_KEY"] = "test-key"
        try:
            from core.providers.proxy_server import create_proxy_app
            app = create_proxy_app(data_dir="")
            from starlette.testclient import TestClient
            client = TestClient(app)
            resp = client.get("/v1/status")
            assert resp.status_code == 200
            data = resp.json()
            providers = data.get("providers", {})
            assert "tokenrouter-qwen" in providers, f"tokenrouter-qwen not in {list(providers.keys())}"
            assert "tokenrouter-nvidia" in providers, f"tokenrouter-nvidia not in {list(providers.keys())}"
        finally:
            os.environ.pop("TOKENROUTER_API_KEY", None)

    def test_expected_local_providers_present(self):
        from core.providers.proxy_server import create_proxy_app
        app = create_proxy_app(data_dir="")
        from starlette.testclient import TestClient
        client = TestClient(app)
        resp = client.get("/v1/status")
        data = resp.json()
        providers = data.get("providers", {})
        expected = ["local", "local-gemma12b", "local-qwen8b", "local-qwen32b"]
        for name in expected:
            assert name in providers, f"{name} missing from {list(providers.keys())}"


class TestRoutingOrder:
    def test_speed_order_preferred_cloud_over_local(self):
        providers = [_make_provider("local"), _make_provider("groq"),
                     _make_provider("openrouter"), _make_provider("tokenrouter-qwen")]
        bt = MagicMock()
        rlt = MagicMock()
        routing = {c: ["groq", "openrouter", "tokenrouter-qwen", "local"]
                   for c in ["low", "medium", "high", "critical"]}
        router = Router(providers=providers, budget=bt, routing=routing,
                        rate_limit_tracker=rlt)
        req = LLMRequest(prompt="hello", complexity=Complexity.LOW)
        result = router.route(req)
        assert result.provider == "groq", f"Expected groq first, got {result.provider}"

    def test_fallback_when_provider_unavailable(self):
        groq = _make_provider("groq", available=False)
        samba = _make_provider("sambanova")
        local = _make_provider("local")
        bt = MagicMock()
        rlt = MagicMock()
        routing = {c: ["groq", "sambanova", "local"] for c in ["low", "medium", "high", "critical"]}
        router = Router(providers=[groq, samba, local], budget=bt, routing=routing,
                        rate_limit_tracker=rlt)
        req = LLMRequest(prompt="hello", complexity=Complexity.LOW)
        result = router.route(req)
        assert result.provider == "sambanova", f"Expected sambanova fallback, got {result.provider}"

    def test_local_only_when_all_cloud_down(self):
        groq = _make_provider("groq", available=False)
        openrouter = _make_provider("openrouter", available=False)
        tokenrouter = _make_provider("tokenrouter-qwen", available=False)
        local = _make_provider("local")
        bt = MagicMock()
        rlt = MagicMock()
        routing = {c: ["groq", "openrouter", "tokenrouter-qwen", "local"]
                   for c in ["low", "medium", "high", "critical"]}
        router = Router(providers=[groq, openrouter, tokenrouter, local], budget=bt,
                        routing=routing, rate_limit_tracker=rlt)
        req = LLMRequest(prompt="hello", complexity=Complexity.LOW)
        result = router.route(req)
        assert result.provider == "local", f"Expected local fallback, got {result.provider}"


class TestCircuitBreaker:
    def test_status_shows_circuit_breaker_states(self):
        from core.providers.proxy_server import create_proxy_app
        app = create_proxy_app(data_dir="")
        from starlette.testclient import TestClient
        client = TestClient(app)
        resp = client.get("/v1/status")
        data = resp.json()
        assert "circuit_states" in data
        assert len(data["circuit_states"]) > 0


class TestProviderStatus:
    def test_status_endpoint_returns_routing_order(self):
        from core.providers.proxy_server import create_proxy_app
        app = create_proxy_app(data_dir="")
        from starlette.testclient import TestClient
        client = TestClient(app)
        resp = client.get("/v1/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "routing_order" in data
        order = data["routing_order"]
        assert "groq" in order
        assert order.index("groq") < order.index("local"), "Cloud should rank before local"

    def test_health_endpoint_responds(self):
        from core.providers.proxy_server import create_proxy_app
        app = create_proxy_app(data_dir="")
        from starlette.testclient import TestClient
        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_routing_order_matches_speed_order(self):
        from core.providers.proxy_server import create_proxy_app
        app = create_proxy_app(data_dir="")
        from starlette.testclient import TestClient
        client = TestClient(app)
        resp = client.get("/v1/status")
        data = resp.json()
        order = data["routing_order"]
        assert order.index("groq") < order.index("openrouter") < order.index("tokenrouter-qwen") < order.index("local")
