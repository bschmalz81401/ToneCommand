"""The OpenAI-compatible backend, against a stub server on loopback.

No proxy, no subscription, no outbound network: a threaded http.server stands
in for CLIProxyAPI (or a local LLM), so every failure class below is
reproducible in CI.
"""
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from fm9 import planner

PLAN = {"summary": "more gain", "actions": [
    {"kind": "set_param", "block": "amp", "param": "DISTORT_DRIVE",
     "value": 6.0, "reason": "asked for gain"}]}


class Stub:
    """One canned reply, plus a record of what the backend actually sent."""

    def __init__(self, status=200, body=None, raw=None):
        self.status, self.body, self.raw = status, body, raw
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("content-length", 0))
                outer.requests.append({
                    "path": self.path,
                    "headers": dict(self.headers),
                    "body": json.loads(self.rfile.read(length) or b"{}"),
                })
                payload = (outer.raw if outer.raw is not None
                           else json.dumps(outer.body).encode())
                self.send_response(outer.status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *a):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"

    def __enter__(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


def reply(content, model="stub-model"):
    return {"model": model, "choices": [{"message": {"content": content}}]}


def ask(monkeypatch, url, **env):
    monkeypatch.setenv("PLANNER_BASE_URL", url)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return planner._plan_via_openai("more gain", "STATE", "REFERENCE")


def test_a_plan_comes_back_with_the_model_the_server_reported(monkeypatch):
    with Stub(body=reply(json.dumps(PLAN))) as stub:
        got, model = ask(monkeypatch, stub.url)
    assert got["actions"][0]["param"] == "DISTORT_DRIVE"
    assert model == "stub-model"


def test_it_posts_chat_completions_with_the_real_prompt(monkeypatch):
    with Stub(body=reply(json.dumps(PLAN))) as stub:
        ask(monkeypatch, stub.url, PLANNER_MODEL="llama3.3")
        sent = stub.requests[0]
    assert sent["path"] == "/v1/chat/completions"
    assert sent["body"]["model"] == "llama3.3"
    system, user = sent["body"]["messages"]
    assert planner.JSON_ONLY in system["content"]
    assert "REFERENCE" in user["content"] and "STATE" in user["content"]
    assert "more gain" in user["content"]


def test_the_api_key_is_optional_by_design(monkeypatch):
    """An OAuth router authenticates upstream and often wants no key."""
    monkeypatch.delenv("PLANNER_API_KEY", raising=False)
    with Stub(body=reply(json.dumps(PLAN))) as stub:
        ask(monkeypatch, stub.url)
        assert "authorization" not in {k.lower() for k in stub.requests[0]["headers"]}
    with Stub(body=reply(json.dumps(PLAN))) as stub:
        ask(monkeypatch, stub.url, PLANNER_API_KEY="sk-local")
        headers = {k.lower(): v for k, v in stub.requests[0]["headers"].items()}
        assert headers["authorization"] == "Bearer sk-local"


def test_json_buried_in_prose_is_still_extracted(monkeypatch):
    with Stub(body=reply(f"Sure thing!\n```json\n{json.dumps(PLAN)}\n```")) as stub:
        got, _ = ask(monkeypatch, stub.url)
    assert got["summary"] == "more gain"


def test_content_as_a_list_of_parts(monkeypatch):
    body = {"choices": [{"message": {"content": [
        {"text": json.dumps(PLAN)[:20]}, {"text": json.dumps(PLAN)[20:]}]}}]}
    with Stub(body=body) as stub:
        got, _ = ask(monkeypatch, stub.url)
    assert got["actions"][0]["value"] == 6.0


def test_reasoning_content_is_used_when_content_is_empty(monkeypatch):
    """Local reasoning models spend the budget there and leave content blank."""
    body = {"choices": [{"message": {"content": "",
                                     "reasoning_content": json.dumps(PLAN)}}]}
    with Stub(body=body) as stub:
        got, _ = ask(monkeypatch, stub.url)
    assert got["summary"] == "more gain"


# --- failure classes ---

def test_no_base_url_is_unavailable(monkeypatch):
    monkeypatch.setenv("PLANNER_BASE_URL", "")
    with pytest.raises(planner.BackendFailure) as err:
        planner._plan_via_openai("p", "s", "r")
    assert err.value.failure_class == "unavailable"


def test_a_refusal_reports_status_and_body(monkeypatch):
    with Stub(status=502, raw=b'{"error":"upstream oauth expired"}') as stub:
        with pytest.raises(planner.BackendFailure) as err:
            ask(monkeypatch, stub.url)
    assert err.value.failure_class == "http_status"
    assert "502" in err.value.detail and "oauth expired" in err.value.detail


def test_a_non_json_body_is_unreadable_output(monkeypatch):
    with Stub(raw=b"<html>gateway</html>") as stub:
        with pytest.raises(planner.BackendFailure) as err:
            ask(monkeypatch, stub.url)
    assert err.value.failure_class == "unreadable_output"


def test_prose_with_no_json_object_is_unreadable_output(monkeypatch):
    with Stub(body=reply("I would rather not.")) as stub:
        with pytest.raises(planner.BackendFailure) as err:
            ask(monkeypatch, stub.url)
    assert err.value.failure_class == "unreadable_output"


def test_an_empty_reply_says_what_to_change(monkeypatch):
    with Stub(body={"choices": [{"message": {"content": ""}}]}) as stub:
        with pytest.raises(planner.BackendFailure) as err:
            ask(monkeypatch, stub.url)
    assert err.value.failure_class == "empty_output"
    assert "PLANNER_MAX_TOKENS" in err.value.detail


def test_a_configured_but_dead_router_fails_fast_and_names_itself(monkeypatch):
    """The concern raised on #20: a router that is not running must not hang."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    dead = f"http://127.0.0.1:{sock.getsockname()[1]}/v1"
    sock.close()
    with pytest.raises(planner.BackendFailure) as err:
        ask(monkeypatch, dead)
    assert err.value.failure_class == "transport"
    assert err.value.target == dead
