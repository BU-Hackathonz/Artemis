def pytest_configure(config):
    config.addinivalue_line(
        "markers", "live: calls the real Claude API; skipped unless ANTHROPIC_API_KEY is set"
    )
