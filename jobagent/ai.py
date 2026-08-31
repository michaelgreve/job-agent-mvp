from __future__ import annotations

import json
import os
import re
from abc import ABC, abstractmethod
from typing import Any

import httpx
from pydantic import BaseModel, Field, ValidationError


class AIError(RuntimeError):
    pass


class GeneratedApplication(BaseModel):
    application: str = ""
    facts_used: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    provider: str
    model: str = ""
    requires_external_response: bool = False
    handoff_prompt: str = ""


APPLICATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "application": {"type": "string"},
        "facts_used": {"type": "array", "items": {"type": "string"}},
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["application", "facts_used", "warnings"],
    "additionalProperties": False,
}

SYSTEM_INSTRUCTIONS = (
    "Du er en præcis dansk jobansøgningsassistent. "
    "Du må kun bruge fakta, der står i kandidatprofilen. "
    "Opfind aldrig erfaring, uddannelse, værktøjer, resultater, motivation eller personlige forhold. "
    "Skriv naturligt og konkret uden rekrutteringsfloskler."
)


def _extract_openai_text(payload: dict[str, Any]) -> str:
    if payload.get("output_text"):
        return str(payload["output_text"])
    pieces: list[str] = []
    for item in payload.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if not isinstance(content, dict):
                continue
            if content.get("type") in {"output_text", "text"} and content.get("text"):
                pieces.append(str(content["text"]))
    return "\n".join(pieces).strip()


def _strip_code_fence(text: str) -> str:
    cleaned = text.strip()
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", cleaned, flags=re.I | re.S)
    return match.group(1).strip() if match else cleaned


def _parse_generated(text: str, provider: str, model: str) -> GeneratedApplication:
    cleaned = _strip_code_fence(text)
    try:
        raw = json.loads(cleaned)
        if not isinstance(raw, dict):
            raise ValueError("JSON-svaret er ikke et objekt")
        parsed = GeneratedApplication(
            application=str(raw.get("application") or "").strip(),
            facts_used=[str(x).strip() for x in raw.get("facts_used", []) if str(x).strip()],
            warnings=[str(x).strip() for x in raw.get("warnings", []) if str(x).strip()],
            provider=provider,
            model=model,
        )
        if not parsed.application:
            raise ValueError("Ansøgningsteksten mangler")
        return parsed
    except (json.JSONDecodeError, ValidationError, ValueError, TypeError):
        # Older or smaller local models occasionally ignore structured-output instructions.
        # Keep the useful text instead of throwing it away, but make the loss visible.
        if not cleaned:
            raise AIError(f"{provider} returnerede ingen ansøgningstekst")
        return GeneratedApplication(
            application=cleaned,
            facts_used=[],
            warnings=["Modellen returnerede almindelig tekst i stedet for det aftalte JSON-format; faktatjeklisten kunne derfor ikke udlæses."],
            provider=provider,
            model=model,
        )


class ApplicationProvider(ABC):
    name: str

    def __init__(self, config: dict[str, Any]):
        self.config = config

    @abstractmethod
    async def generate(self, prompt: str, *, template_result: GeneratedApplication | None = None) -> GeneratedApplication:
        raise NotImplementedError


class TemplateProvider(ApplicationProvider):
    name = "template"

    async def generate(self, prompt: str, *, template_result: GeneratedApplication | None = None) -> GeneratedApplication:
        if template_result is None:
            raise AIError("Skabelonresultatet mangler")
        return template_result


class ClipboardProvider(ApplicationProvider):
    name = "clipboard"

    async def generate(self, prompt: str, *, template_result: GeneratedApplication | None = None) -> GeneratedApplication:
        handoff = f"""Jeg vil gerne have skrevet en målrettet jobansøgning ud fra pakken nedenfor.

Returnér kun den færdige ansøgningstekst, uden analyse, markdown eller forklaring. Brug cirka to afsnit plus en kort afslutning, og opfind ingen fakta.

--- START PÅ ANSØGNINGSPAKKE ---
{prompt}
--- SLUT PÅ ANSØGNINGSPAKKE ---"""
        return GeneratedApplication(
            provider=self.name,
            requires_external_response=True,
            handoff_prompt=handoff,
        )


class OpenAIProvider(ApplicationProvider):
    name = "openai"

    async def generate(self, prompt: str, *, template_result: GeneratedApplication | None = None) -> GeneratedApplication:
        key = os.getenv("OPENAI_API_KEY", "").strip()
        if not key:
            raise AIError("OPENAI_API_KEY mangler i .env eller miljøvariabler")
        model = str(self.config.get("openai_model") or "gpt-5-mini").strip()
        url = str(self.config.get("openai_url") or "https://api.openai.com/v1").rstrip("/")
        request_body: dict[str, Any] = {
            "model": model,
            "instructions": SYSTEM_INSTRUCTIONS,
            "input": prompt,
            "max_output_tokens": int(self.config.get("max_output_tokens", 1600)),
            "store": False,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "job_application",
                    "strict": True,
                    "schema": APPLICATION_SCHEMA,
                }
            },
        }
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=180) as client:
            response = await client.post(f"{url}/responses", headers=headers, json=request_body)
            # Some models or compatible gateways may not support Responses structured
            # outputs yet. Retry once with explicit JSON instructions.
            if response.status_code == 400:
                fallback = dict(request_body)
                fallback.pop("text", None)
                fallback["input"] = prompt + "\n\nReturnér et JSON-objekt med felterne application, facts_used og warnings."
                response = await client.post(f"{url}/responses", headers=headers, json=fallback)
            if response.status_code >= 400:
                raise AIError(f"OpenAI-fejl {response.status_code}: {response.text[:700]}")
            text = _extract_openai_text(response.json())
        return _parse_generated(text, self.name, model)


class OllamaProvider(ApplicationProvider):
    name = "ollama"

    async def generate(self, prompt: str, *, template_result: GeneratedApplication | None = None) -> GeneratedApplication:
        model = str(self.config.get("ollama_model") or "qwen3:8b").strip()
        base = str(self.config.get("ollama_url") or "http://localhost:11434").rstrip("/")
        payload = {
            "model": model,
            "stream": False,
            "messages": [
                {"role": "system", "content": SYSTEM_INSTRUCTIONS},
                {"role": "user", "content": prompt},
            ],
            "format": APPLICATION_SCHEMA,
            "options": {"temperature": float(self.config.get("temperature", 0.2))},
        }
        async with httpx.AsyncClient(timeout=300) as client:
            response = await client.post(f"{base}/api/chat", json=payload)
            if response.status_code == 400:
                # Older Ollama versions understand JSON mode but not a full schema.
                fallback = dict(payload)
                fallback["format"] = "json"
                fallback["messages"] = list(payload["messages"])
                fallback["messages"][-1] = {
                    "role": "user",
                    "content": prompt + "\n\nReturnér et JSON-objekt med felterne application, facts_used og warnings.",
                }
                response = await client.post(f"{base}/api/chat", json=fallback)
            if response.status_code >= 400:
                raise AIError(f"Ollama-fejl {response.status_code}: {response.text[:700]}")
            data = response.json()
            text = str(data.get("message", {}).get("content", "")).strip()
        return _parse_generated(text, self.name, model)


def get_provider(name: str, config: dict[str, Any]) -> ApplicationProvider:
    providers: dict[str, type[ApplicationProvider]] = {
        "template": TemplateProvider,
        "clipboard": ClipboardProvider,
        "openai": OpenAIProvider,
        "ollama": OllamaProvider,
    }
    provider_type = providers.get(str(name or "template").casefold())
    if provider_type is None:
        raise AIError(f"Ukendt ansøgningsgenerator: {name}")
    return provider_type(config)


async def generate_application(
    provider_name: str,
    prompt: str,
    config: dict[str, Any],
    *,
    template_result: GeneratedApplication | None = None,
) -> GeneratedApplication:
    provider = get_provider(provider_name, config)
    return await provider.generate(prompt, template_result=template_result)


async def list_ollama_models(config: dict[str, Any]) -> list[dict[str, Any]]:
    base = str(config.get("ollama_url") or "http://localhost:11434").rstrip("/")
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            response = await client.get(f"{base}/api/tags")
        except httpx.HTTPError as exc:
            raise AIError(f"Kunne ikke forbinde til Ollama på {base}: {exc}") from exc
    if response.status_code >= 400:
        raise AIError(f"Ollama-fejl {response.status_code}: {response.text[:500]}")
    models = response.json().get("models", [])
    return [m for m in models if isinstance(m, dict) and (m.get("name") or m.get("model"))]


async def test_provider_connection(provider_name: str, config: dict[str, Any]) -> dict[str, Any]:
    provider_name = provider_name.casefold()
    if provider_name in {"template", "clipboard"}:
        return {"ok": True, "provider": provider_name, "message": "Kræver ingen ekstern forbindelse."}
    if provider_name == "ollama":
        models = await list_ollama_models(config)
        names = [str(m.get("name") or m.get("model")) for m in models]
        configured = str(config.get("ollama_model") or "")
        message = f"Forbundet til Ollama. {len(names)} lokale modeller fundet."
        if configured and configured not in names:
            message += f" Den valgte model ‘{configured}’ er ikke installeret."
        return {"ok": True, "provider": provider_name, "message": message, "models": names}
    if provider_name == "openai":
        key = os.getenv("OPENAI_API_KEY", "").strip()
        if not key:
            raise AIError("OPENAI_API_KEY mangler i .env eller miljøvariabler")
        model = str(config.get("openai_model") or "gpt-5-mini").strip()
        url = str(config.get("openai_url") or "https://api.openai.com/v1").rstrip("/")
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                f"{url}/models/{model}",
                headers={"Authorization": f"Bearer {key}"},
            )
        if response.status_code >= 400:
            raise AIError(f"OpenAI-fejl {response.status_code}: {response.text[:500]}")
        return {"ok": True, "provider": provider_name, "model": model, "message": f"OpenAI-forbindelsen virker, og modellen ‘{model}’ kan findes."}
    raise AIError(f"Ukendt ansøgningsgenerator: {provider_name}")
