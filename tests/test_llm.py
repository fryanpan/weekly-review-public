from weekly_review.llm import LLMClient, CachedLLMClient


class _FakeMessages:
    def __init__(self, calls):
        self.calls = calls

    def create(self, **kwargs):
        self.calls.append(kwargs)

        class Block:
            type = "text"
            text = '{"est_human_hours": 3.5}'

        class Usage:
            input_tokens = 100
            output_tokens = 20
            cache_read_input_tokens = 0
            cache_creation_input_tokens = 0

        class Response:
            content = [Block()]
            usage = Usage()
            stop_reason = "end_turn"

        return Response()


class _FakeClient:
    def __init__(self, api_key=None):
        self.messages = _FakeMessages(calls=[])


def test_llm_client_calls_anthropic(monkeypatch):
    monkeypatch.setattr("weekly_review.llm.Anthropic", _FakeClient)
    client = LLMClient(api_key="fake")
    out = client.complete(
        system="you are an estimator",
        user="task: x",
        model="claude-sonnet-4-6",
    )
    assert out["text"] == '{"est_human_hours": 3.5}'
    assert out["usage"]["input_tokens"] == 100
    assert out["model"] == "claude-sonnet-4-6"


def test_cached_client_skips_second_call(tmp_path, monkeypatch):
    monkeypatch.setattr("weekly_review.llm.Anthropic", _FakeClient)
    inner = LLMClient(api_key="fake")
    cached = CachedLLMClient(inner, cache_dir=tmp_path)

    r1 = cached.complete(system="s", user="u", model="claude-haiku-4-5")
    r2 = cached.complete(system="s", user="u", model="claude-haiku-4-5")
    assert r1["text"] == r2["text"]
    # calls list lives on the inner fake messages; grab it
    calls = inner._client.messages.calls
    assert len(calls) == 1  # second call was served from cache


def test_prompt_caching_applied_for_long_system(monkeypatch):
    monkeypatch.setattr("weekly_review.llm.Anthropic", _FakeClient)
    client = LLMClient(api_key="fake")
    long_system = "x" * 5000  # > 4096 char threshold
    client.complete(system=long_system, user="u", model="claude-haiku-4-5")
    calls = client._client.messages.calls
    system_blocks = calls[0]["system"]
    assert isinstance(system_blocks, list)
    assert system_blocks[0].get("cache_control") == {"type": "ephemeral"}
