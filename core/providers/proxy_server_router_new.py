    def _get_router():
        if "router" in _router_cache:
            return _router_cache["router"]

        from core.providers.budget import BudgetTracker
        from core.providers.router import Router
        from core.providers.llamacpp import LlamaCppProvider
        from core.providers.groq import GroqProvider
        from core.providers.google import GoogleProvider
        from core.providers.cerebras import CerebrasProvider
        from core.providers.sambanova import SambaNovaProvider
        from core.providers.openrouter import OpenRouterProvider
        from core.providers.ollama_cloud import OllamaCloudProvider
        from core.providers.openai_compat import OpenAICompatProvider

        bt = BudgetTracker(data_dir=data_dir)
        providers = []
        
        # OpenRouter Models (Prioritized as requested)
        if os.environ.get("OPENROUTER_API_KEY"):
            # 1. Owl-Alpha (Hermes 3 405B) - Paid tier
            providers.append(OpenRouterProvider(
                model=os.environ.get("OWL_MODEL", "nousresearch/hermes-3-llama-3.1-405b"),
                name="owl",
                daily_limit=50000
            ))
            # 2. Laguna-M.1 - Free tier/preferred
            providers.append(OpenRouterProvider(
                model=os.environ.get("LAGUNA_MODEL", "poolside/laguna-m.1:free"),
                name="laguna",
                daily_limit=5000
            ))
            # 3. Generic OpenRouter (will use default or free models)
            providers.append(OpenRouterProvider(
                name="openrouter",
                daily_limit=10000
            ))

        # Cloud free tiers / high performance
        if os.environ.get("GOOGLE_API_KEY"):
            providers.append(GoogleProvider(
                model="gemini-2.0-flash",
                name="google-alt",
                daily_limit=1500
            ))
        if os.environ.get("GROQ_API_KEY"):
            providers.append(GroqProvider())
        if os.environ.get("CEREBRAS_API_KEY"):
            providers.append(CerebrasProvider())
        if os.environ.get("SAMBANOVA_API_KEY"):
            providers.append(SambaNovaProvider())

        # Local LLM (Disaster recovery only — too slow for main loop)
        local = LlamaCppProvider(
            name="local",
            model="qwen2.5:14b",
            timeout=300,
            endpoint=os.environ.get("LOCAL_LLM_URL", "http://localhost:8081"),
        )
        providers.append(local)

        provider_names = [p.name for p in providers]
        log.info(f"LLM Proxy initialized with providers: {provider_names}")

        # Routing Table (Prioritizing Paid/Credit models first)
        tier_order = ["owl", "laguna", "google-alt", "groq", "cerebras", "sambanova", "local"]
        
        router = Router(
            providers=providers,
            budget=bt,
            routing={
                "low": tier_order,
                "medium": tier_order,
                "high": tier_order,
                "critical": tier_order,
            },
        )
        _router_cache["router"] = router
        _router_cache["budget"] = bt
        return router
