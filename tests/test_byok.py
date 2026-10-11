"""Bring your own key: any model works. A model without adaptive thinking or effort gets a call without them."""

from __future__ import annotations

from types import SimpleNamespace

import anthropic
import httpx

from nightwatch.agent import Agent


class FakeStream:
    def __init__(self, msg):
        self.msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        return iter(())

    def get_final_message(self):
        return self.msg


def test_model_without_thinking_gets_a_call_without_it():
    calls, logged = [], []

    def stream(**kw):
        calls.append(kw)
        if "thinking" in kw:
            r = httpx.Response(400, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))
            raise anthropic.BadRequestError("thinking: adaptive thinking is not supported on this model", response=r, body=None)
        return FakeStream("ok")

    fake = SimpleNamespace(client=SimpleNamespace(messages=SimpleNamespace(stream=stream)),
                           s=SimpleNamespace(model="some-model", max_tokens=100, effort="high"),
                           system="x", tools=[], _thinking=True,
                           audit=SimpleNamespace(log=lambda kind, payload, iid: logged.append(kind)))
    fake._call_model = lambda *a, **k: Agent._call_model(fake, *a, **k)
    assert Agent._call_model(fake, [{"role": "user", "content": "hi"}], "inc-1") == "ok"
    assert "thinking" in calls[0] and "thinking" not in calls[1] and fake._thinking is False
    assert logged == ["llm_feature_off"]
