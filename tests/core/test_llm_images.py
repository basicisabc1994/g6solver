# SPDX-License-Identifier: Apache-2.0
"""Images on the model door (FW-11), beside ``complete`` and never changing it: a checked :class:`~g6.core.llm.Image`,
``complete_with_images`` on the three real callers and the fake, ``EmptyRetry`` passing images through, and
``supports_images``. All offline, through transport doubles."""
from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest

from g6.core import llm

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16
GIF = b"GIF89a" + b"\x00" * 16
WEBP = b"RIFF\x10\x00\x00\x00WEBPVP8 " + b"\x00" * 8
B64 = base64.b64encode(PNG).decode("ascii")


@pytest.mark.parametrize(("data", "media_type"), [(PNG, "image/png"), (JPEG, "image/jpeg"), (GIF, "image/gif"),
                                                  (WEBP, "image/webp")])
def test_an_image_is_recognised_by_its_bytes(data: bytes, media_type: str) -> None:
    assert llm.Image.from_bytes(data).media_type == media_type
    assert llm.Image(data, media_type).data == data


def test_bytes_that_are_not_the_declared_kind_are_refused() -> None:
    with pytest.raises(ValueError, match="image/png"):
        llm.Image(JPEG, "image/png")
    with pytest.raises(ValueError, match="png, jpeg, gif or webp"):
        llm.Image.from_bytes(b"%PDF-1.7 not an image")
    with pytest.raises(ValueError, match="image/tiff"):
        llm.Image(PNG, "image/tiff")


def test_an_image_has_a_size_limit() -> None:
    with pytest.raises(ValueError, match="bytes"):
        llm.Image(PNG + b"\x00" * llm.MAX_IMAGE_BYTES, "image/png")


def _posts(monkeypatch: pytest.MonkeyPatch, reply: dict) -> list[tuple[str, dict]]:
    seen: list[tuple[str, dict]] = []

    def post(url: str, body: dict, headers: dict, timeout: float) -> dict:
        seen.append((url, body))
        return reply

    monkeypatch.setattr(llm, "_post_json", post)
    return seen


def test_ollama_sends_the_images_with_the_user_message(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _posts(monkeypatch, {"message": {"content": "a cat"}})
    out = llm.OllamaCaller("llava", base_url="http://127.0.0.1:9").complete_with_images(
        "sys", "what is this?", [llm.Image(PNG, "image/png")], max_tokens=50)
    assert out == "a cat"
    url, body = seen[0]
    assert url == "http://127.0.0.1:9/api/chat"
    assert body["messages"][1] == {"role": "user", "content": "what is this?", "images": [B64]}
    assert body["options"]["num_predict"] == 50


def test_an_openai_compatible_endpoint_gets_content_parts(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _posts(monkeypatch, {"choices": [{"message": {"content": "a cat"}}]})
    out = llm.OpenAICompatibleCaller("m", base_url="http://x/v1", api_key=None).complete_with_images(
        "sys", "what is this?", [llm.Image(PNG, "image/png")])
    assert out == "a cat"
    assert seen[0][1]["messages"][1] == {"role": "user", "content": [
        {"type": "text", "text": "what is this?"},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{B64}"}}]}


def test_anthropic_gets_image_blocks_before_the_text() -> None:
    captured: dict = {}

    def create(**kw):  # noqa: ANN003
        captured.update(kw)
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="a cat")])

    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))
    out = llm.AnthropicCaller(client=client).complete_with_images("sys", "what is this?", [llm.Image(PNG, "image/png")])
    assert out == "a cat"
    assert captured["messages"] == [{"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": B64}},
        {"type": "text", "text": "what is this?"}]}]
    assert captured["fallbacks"] == "default"


def test_an_anthropic_refusal_with_images_still_raises() -> None:
    response = SimpleNamespace(stop_reason="refusal", stop_details=SimpleNamespace(category="cyber"), content=[])
    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=lambda **k: response)))
    with pytest.raises(llm.LLMError, match="G6_E_MODEL_REFUSED"):
        llm.AnthropicCaller(client=client).complete_with_images("s", "u", [llm.Image(PNG, "image/png")])


def test_image_calls_obey_the_kill_switch_and_are_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    from g6.core.model_calls import counting
    seen = _posts(monkeypatch, {"message": {"content": "x"}})
    image = [llm.Image(PNG, "image/png")]
    with counting() as calls:
        llm.OllamaCaller("m").complete_with_images("s", "u", image)
        monkeypatch.setenv("G6_DISABLE_LLM", "1")
        with pytest.raises(llm.LLMError, match="G6_E_LLM_DISABLED"):
            llm.OllamaCaller("m").complete_with_images("s", "u", image)
    assert calls() == 1 and len(seen) == 1


@pytest.mark.parametrize("images", [[], [PNG], ["not an image"]])
def test_images_must_be_a_non_empty_list_of_images(images: list, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _posts(monkeypatch, {"message": {"content": "x"}})
    with pytest.raises(ValueError, match="Image"):
        llm.OllamaCaller("m").complete_with_images("s", "u", images)
    assert seen == []


def test_supports_images_names_the_callers_that_can_send_them() -> None:
    assert llm.supports_images(llm.OllamaCaller("m"))
    assert llm.supports_images(llm.OpenAICompatibleCaller("m", base_url="http://x", api_key=None))
    assert llm.supports_images(llm.AnthropicCaller(client=object()))
    assert llm.supports_images(llm.FakeLLM())
    assert llm.supports_images(llm.EmptyRetry(llm.OllamaCaller("m")))
    assert not llm.supports_images(llm.EmptyRetry(llm.NoModel()))
    assert not llm.supports_images(llm.NoModel())
    assert not llm.supports_images(llm.FallbackCaller([llm.OllamaCaller("m")]))

    class TextOnly:
        def complete(self, system: str, user: str, *, max_tokens: int = llm.DEFAULT_MAX_TOKENS) -> str:
            return "x"

    assert not llm.supports_images(TextOnly())


def test_empty_retry_escalates_an_empty_image_answer() -> None:
    budgets: list[int] = []

    class Seeing:
        accepts_images = True

        def complete(self, system: str, user: str, *, max_tokens: int = llm.DEFAULT_MAX_TOKENS) -> str:
            raise AssertionError("the text door was used")

        def complete_with_images(self, system, user, images, *, max_tokens=llm.DEFAULT_MAX_TOKENS):  # noqa: ANN001
            budgets.append(max_tokens)
            return "" if len(budgets) == 1 else "a cat"

    out = llm.EmptyRetry(Seeing()).complete_with_images("s", "u", [llm.Image(PNG, "image/png")], max_tokens=100)
    assert (out, budgets) == ("a cat", [100, 8192])


def test_empty_retry_over_a_text_only_caller_refuses_images() -> None:
    with pytest.raises(llm.LLMError, match="G6_E_LLM_NO_IMAGES"):
        llm.EmptyRetry(llm.NoModel()).complete_with_images("s", "u", [llm.Image(PNG, "image/png")])


def test_the_fake_records_the_images_it_was_shown() -> None:
    fake = llm.FakeLLM(default="a cat")
    image = llm.Image(PNG, "image/png")
    assert fake.complete_with_images("s", "u", [image]) == "a cat"
    assert fake.calls == [("s", "u")] and fake.images == [(image,)]
