"""The Guard (contracts.md §6): token on every route, constant-time compare, Host and Origin checks.

POST /desktop/{op_id}/result can make the core believe typing happened, so no route may be open.
There is no intentionally open route: /health, /docs and /openapi.json all need the token too.
"""

import logging
import re

import httpx
import pytest
from starlette.routing import Route

import artemis.core.app as app_module
from artemis.agent.stub import StubAgent
from artemis.core.app import Core, create_app, host_allowed
from artemis.policy.testing import FakeRunner
from artemis.voice.stub import StubVoice

TOKEN = "guard-test-token-4f9a1c"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def core(tmp_path):
    return Core(voice=StubVoice(), make_runner=FakeRunner, make_agent=StubAgent,
                projects_dir=tmp_path / "projects", log_dir=tmp_path / "logs")


@pytest.fixture
async def app(core):
    app = create_app(core, TOKEN, warm_voice=False)
    async with app.router.lifespan_context(app):
        yield app


def client_for(app, base="http://127.0.0.1:8765"):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=base)


def all_routes(app):
    """(method, concrete path) for every route the app serves, including the built-in docs."""
    out = []
    for route in app.routes:
        assert isinstance(route, Route), f"{route!r}: add a websocket/mount case to this test"
        path = re.sub(r"\{[^}]+\}", "x", route.path)
        for method in sorted((route.methods or set()) - {"HEAD", "OPTIONS"}):
            out.append((method, path))
    return out


def test_the_route_list_is_not_empty(app):
    routes = all_routes(app)
    assert ("POST", "/desktop/x/result") in routes and ("GET", "/health") in routes
    assert len(routes) >= 15


async def test_every_route_rejects_missing_and_wrong_tokens(app):
    async with client_for(app) as c:
        for method, path in all_routes(app):
            for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": TOKEN},
                            {"Authorization": f"Basic {TOKEN}"}, {"Authorization": f"bearer {TOKEN}"},
                            {"Authorization": f"Bearer {TOKEN} "}, {"Authorization": f"Bearer {TOKEN}x"},
                            {"Authorization": "Bearer "}):
                r = await c.request(method, path, headers=headers, json={} if method != "GET" else None)
                assert r.status_code == 401, (method, path, headers, r.status_code)
                assert r.json() == {"error": "unauthorized"}
                assert TOKEN not in r.text


async def test_query_string_and_cookie_tokens_do_not_authenticate(app):
    async with client_for(app) as c:
        assert (await c.get(f"/health?token={TOKEN}")).status_code == 401
        assert (await c.get("/health", headers={"Cookie": f"token={TOKEN}"})).status_code == 401
        assert (await c.get("/health", headers={"X-Artemis-Token": TOKEN})).status_code == 401


async def test_the_right_token_gets_through_and_no_route_is_open(app):
    async with client_for(app) as c:
        assert (await c.get("/health", headers=AUTH)).status_code == 200
        r = await c.post("/desktop/nope/result", headers=AUTH, json={})
        assert r.status_code != 401 and r.status_code != 403  # reaches the route (404/422 from its own checks)


async def test_unknown_paths_also_need_the_token(app):
    async with client_for(app) as c:
        assert (await c.get("/no-such-route")).status_code == 401  # no route-existence oracle
        assert (await c.get("/no-such-route", headers=AUTH)).status_code == 404


async def test_token_compare_is_constant_time(app, monkeypatch):
    calls = []
    real = app_module.hmac.compare_digest

    def spy(a, b):
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(app_module.hmac, "compare_digest", spy)
    async with client_for(app) as c:
        await c.get("/health", headers={"Authorization": "Bearer wrong"})
        await c.get("/health", headers=AUTH)
    assert len(calls) == 2 and all(isinstance(a, bytes) and isinstance(b, bytes) for a, b in calls)


def test_guard_source_never_compares_the_token_with_equality():
    import inspect

    src = inspect.getsource(app_module.Guard)
    assert "compare_digest" in src
    assert not re.search(r"(==|!=)\s*self\._expected|self\._expected\s*(==|!=)|startswith\(", src)


@pytest.mark.parametrize("host,ok", [
    ("127.0.0.1", True), ("127.0.0.1:8765", True), ("localhost", True), ("localhost:8765", True),
    ("LOCALHOST:1420", True),
    ("evil.example", False), ("evil.example:8765", False), ("127.0.0.1.evil.example", False),
    ("127.0.0.1.evil.example:8765", False), ("localhost.evil.example", False), ("evil.localhost.example", False),
    ("127.0.0.1:8765@evil.example", False), ("127.0.0.1:abc", False), ("", False), ("0.0.0.0:8765", False),
    ("192.168.1.5:8765", False), ("[::1]:8765", False), ("127.0.0.2", False), ("127.0.0.1:80:80", False),
])
def test_host_allowed(host, ok):
    assert host_allowed(host) is ok


async def test_other_host_headers_are_rejected_even_with_the_token(app):
    async with client_for(app) as c:
        for host in ("evil.example", "evil.example:8765", "127.0.0.1.evil.example", "0.0.0.0:8765"):
            for method, path in (("GET", "/health"), ("POST", "/desktop/x/result")):
                r = await c.request(method, path, headers={**AUTH, "Host": host}, json={})
                assert r.status_code == 403 and r.json() == {"error": "host_not_allowed"}, (host, path)
        for host in ("127.0.0.1:8765", "localhost:8765", "127.0.0.1", "localhost"):
            assert (await c.get("/health", headers={**AUTH, "Host": host})).status_code == 200


async def test_the_clients_still_work(app):
    """Tauri's webview (Origin http://tauri.localhost) and the island's http://127.0.0.1:<port> calls
    both send Host 127.0.0.1:<port>; the island is a GNOME extension, so it sends no Origin at all."""
    async with client_for(app, "http://127.0.0.1:8123") as c:
        for origin in ("http://tauri.localhost", "http://127.0.0.1:1420", None):
            h = dict(AUTH, **({"Origin": origin} if origin else {}))
            assert (await c.get("/health", headers=h)).status_code == 200


async def test_preflight_from_other_origins_gets_no_allow_origin(app):
    async with client_for(app) as c:
        for origin in ("https://evil.example", "null", "http://tauri.localhost.evil.example",
                       "http://127.0.0.1:1421", "http://localhost:1420"):
            for path in ("/sessions", "/desktop/x/result", "/health"):
                r = await c.options(path, headers={"Origin": origin, "Access-Control-Request-Method": "POST",
                                                   "Access-Control-Request-Headers": "authorization"})
                assert "access-control-allow-origin" not in r.headers, (origin, path)
                assert "access-control-allow-credentials" not in r.headers
        ok = await c.options("/desktop/x/result", headers={
            "Origin": "http://tauri.localhost", "Access-Control-Request-Method": "POST"})
        assert ok.headers["access-control-allow-origin"] == "http://tauri.localhost"


async def test_real_requests_from_other_origins_are_refused_and_not_allowed_by_cors(app):
    async with client_for(app) as c:
        for origin in ("https://evil.example", "null"):
            r = await c.post("/desktop/x/result", headers={**AUTH, "Origin": origin}, json={})
            assert r.status_code == 403 and "access-control-allow-origin" not in r.headers


async def test_the_token_is_not_in_startup_logs_or_events(core, caplog):
    caplog.set_level(logging.DEBUG)
    app = create_app(core, TOKEN, warm_voice=False)
    async with app.router.lifespan_context(app):
        async with client_for(app) as c:
            sid = (await c.post("/sessions", headers=AUTH)).json()["session_id"]
            await c.get("/health")  # a rejected request is logged by nobody with its header
            await c.get("/health", headers={"Authorization": "Bearer wrong"})
        events = [e.model_dump_json() if hasattr(e, "model_dump_json") else repr(e)
                  for e in core.sessions[sid].bus.history]
    assert events, "the session start events should exist"
    assert not any(TOKEN in e for e in events)
    assert TOKEN not in caplog.text
    assert TOKEN not in repr(app.state.core.__dict__)
    logs = list((core.log_dir).glob("*.jsonl")) if core.log_dir and core.log_dir.exists() else []
    assert not any(TOKEN in p.read_text() for p in logs)


def test_an_empty_token_is_refused_at_startup(core):
    with pytest.raises(RuntimeError):
        create_app(core, "")
