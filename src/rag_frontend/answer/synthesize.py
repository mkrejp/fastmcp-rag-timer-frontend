"""One OpenAI-compatible chat call to Novita."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from rag_frontend.answer.prompt import SYSTEM_PROMPT
from rag_frontend.config import Settings


@dataclass(frozen=True)
class Synthesis:
    """Text returned by the model and the token accounting."""

    text: str
    prompt_tokens: int
    completion_tokens: int
    model: str
    calls: int
    http_status: int


class LlmClient:
    """POST ``{LLM_BASE_URL}/chat/completions`` with the sealed Gemma key."""

    def __init__(self, settings: Settings) -> None:
        """Store the Novita settings. The key is not logged."""
        self._settings = settings

    async def complete(self, user_json: str) -> Synthesis:
        """Call the default model once, then the fallback only if that call fails."""
        try:
            text, prompt, completion = await self._once(
                self._settings.llm_model, user_json
            )
            return Synthesis(text, prompt, completion, self._settings.llm_model, 1, 200)
        except httpx.HTTPError:
            if self._settings.llm_model_fallback == self._settings.llm_model:
                raise
            text, prompt, completion = await self._once(
                self._settings.llm_model_fallback, user_json
            )
            return Synthesis(
                text,
                prompt,
                completion,
                self._settings.llm_model_fallback,
                2,
                200,
            )

    async def _once(self, model: str, user_json: str) -> tuple[str, int, int]:
        """Perform a single chat completion."""
        url = self._settings.llm_base_url.rstrip("/") + "/chat/completions"
        payload = {
            "model": model,
            "temperature": 0,
            "max_tokens": 600,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_json},
            ],
        }
        headers = {"Authorization": f"Bearer {self._settings.llm_api_key}"}
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            body = response.json()
        message = body["choices"][0]["message"]["content"]
        usage = body.get("usage") or {}
        return (
            str(message).strip(),
            int(usage.get("prompt_tokens") or 0),
            int(usage.get("completion_tokens") or 0),
        )


def estimate_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Rough Novita price for the two Gemma models. Unknown models cost 0."""
    rates = {
        "google/gemma-3-27b-it": (0.119, 0.20),
        "google/gemma-3-12b-it": (0.05, 0.10),
    }
    prompt_rate, completion_rate = rates.get(model, (0.0, 0.0))
    cost = prompt_tokens / 1_000_000 * prompt_rate
    cost += completion_tokens / 1_000_000 * completion_rate
    return round(cost, 6)
