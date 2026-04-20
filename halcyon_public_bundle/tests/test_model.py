import json
from urllib import error

import pytest

from halcyon_core.governance.invariants import default_invariant
from halcyon_core.model.client import LocalModelClient, ModelClientError, extract_chat_content
from halcyon_core.model.prompts import build_messages, build_system_prompt
from halcyon_core.model.schemas import normalize_model_content, parse_model_intent


def test_parse_model_intent_accepts_structured_json():
    content = json.dumps(
        {
            "reply": "Got it.",
            "memory_proposals": [
                {"content": "Brad prefers MVL first.", "reason": "stated directly", "tags": ["preference"], "pinned": True}
            ],
            "tool_proposals": [
                {"action_type": "write_file", "target": "x.txt", "payload": {"content": "x"}, "reason": "requested"}
            ],
        }
    )
    intent = parse_model_intent(content)
    assert intent.reply == "Got it."
    assert intent.memory_proposals[0].content == "Brad prefers MVL first."
    assert intent.memory_proposals[0].tags == ("preference",)
    assert intent.memory_proposals[0].pinned is True
    assert intent.tool_proposals[0].action_type == "write_file"
    assert intent.tool_proposals[0].payload == {"content": "x"}



def test_parse_model_intent_extracts_harmony_message_json():
    wrapped = '<|channel|>final <|constrain|>json<|message|>{"reply":"MVL live smoke online","memory_proposals":[],"tool_proposals":[]}'
    intent = parse_model_intent(wrapped)
    assert intent.reply == "MVL live smoke online"
    assert intent.parse_warnings == ()


def test_normalize_model_content_strips_known_message_marker():
    assert normalize_model_content("prefix<|message|>{}") == "{}"

def test_parse_model_intent_accepts_plain_text_with_warning():
    intent = parse_model_intent("plain reply")
    assert intent.reply == "plain reply"
    assert intent.memory_proposals == ()
    assert intent.tool_proposals == ()
    assert intent.parse_warnings == ("model output was plain text",)


def test_parse_model_intent_rejects_non_object_json_to_plain_reply():
    intent = parse_model_intent('["not", "an", "object"]')
    assert intent.reply == '["not", "an", "object"]'
    assert intent.parse_warnings == ("model JSON was not an object",)


def test_extract_chat_content_reads_openai_compatible_shape():
    raw = {"choices": [{"message": {"content": "hello"}}]}
    assert extract_chat_content(raw) == "hello"


def test_extract_chat_content_rejects_missing_content():
    with pytest.raises(ValueError):
        extract_chat_content({"choices": []})


def test_local_model_client_posts_chat_completion_payload_and_parses_intent():
    captured = {}

    def fake_transport(url, payload):
        captured["url"] = url
        captured["payload"] = payload
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps({"reply": "loop alive", "memory_proposals": [], "tool_proposals": []})
                    }
                }
            ]
        }

    client = LocalModelClient("http://localhost:1234/v1", "openai/gpt-oss-20b", transport=fake_transport)
    response = client.complete([{"role": "user", "content": "ping"}])

    assert captured["url"] == "http://localhost:1234/v1/chat/completions"
    assert captured["payload"]["model"] == "openai/gpt-oss-20b"
    assert captured["payload"]["messages"] == [{"role": "user", "content": "ping"}]
    assert response.text == "loop alive"
    assert response.intent.reply == "loop alive"


def test_build_system_prompt_includes_invariant_digest_and_proposal_rules():
    invariant = default_invariant()
    prompt = build_system_prompt(invariant=invariant)
    assert invariant.digest() in prompt
    assert "Do not execute tools." in prompt
    assert "Never claim that proposals were executed" in prompt


def test_build_messages_includes_memory_context_when_present():
    messages = build_messages("system", "hello", memory_context="- memory")
    assert messages[0]["role"] == "system"
    assert "Relevant memory" in messages[0]["content"]
    assert messages[1] == {"role": "user", "content": "hello"}


def test_local_model_client_wraps_http_errors(monkeypatch):
    class FakeHTTPError(error.HTTPError):
        def __init__(self):
            super().__init__("http://local", 400, "Bad Request", {}, None)

        def read(self):
            return b'{"error":"bad request"}'

    def raise_http_error(req, timeout):
        raise FakeHTTPError()

    monkeypatch.setattr("urllib.request.urlopen", raise_http_error)
    client = LocalModelClient("http://localhost:1234/v1", "openai/gpt-oss-20b")

    with pytest.raises(ModelClientError) as exc:
        client.complete([{"role": "user", "content": "ping"}])

    assert exc.value.status_code == 400
    assert "bad request" in exc.value.body
