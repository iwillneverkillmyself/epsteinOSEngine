"""AWS Bedrock runtime client wrapper (Anthropic-style models by default)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import boto3


@dataclass
class BedrockResult:
    summary_markdown: str
    tags: List[Dict[str, Any]]
    raw: Dict[str, Any]


class BedrockClient:
    """
    Minimal Bedrock client.

    Default model is configurable via BEDROCK_MODEL_ID.
    This implementation targets Anthropic models (messages API) which are common on Bedrock.
    """

    def __init__(self, region: Optional[str] = None, model_id: Optional[str] = None):
        self.region = region or os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
        # Default to an ON_DEMAND model and use the Bedrock Converse API for broad compatibility.
        # You can override via BEDROCK_MODEL_ID (including an inference profile ARN).
        self.model_id = model_id or os.getenv("BEDROCK_MODEL_ID") or "amazon.nova-pro-v1:0"
        self._client = boto3.client("bedrock-runtime", region_name=self.region)

    def invoke_json(self, prompt: str, max_tokens: int = 800) -> BedrockResult:
        # Prefer the Bedrock "converse" API when available; it's provider-agnostic.
        text = None
        raw: Dict[str, Any] = {}
        if hasattr(self._client, "converse"):
            resp = self._client.converse(
                modelId=self.model_id,
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                inferenceConfig={"maxTokens": max_tokens, "temperature": 0.2, "topP": 0.9},
            )
            raw = resp  # already JSON-like dict
            try:
                content = resp["output"]["message"]["content"]
                if isinstance(content, list) and content:
                    text = content[0].get("text")
            except Exception:
                text = None

        # Fallback to legacy invoke_model with provider-specific payloads.
        if text is None:
            body = self._build_body(prompt=prompt, max_tokens=max_tokens)
            resp = self._client.invoke_model(
                modelId=self.model_id,
                body=json.dumps(body).encode("utf-8"),
                accept="application/json",
                contentType="application/json",
            )
            payload = resp["body"].read()
            data = json.loads(payload)
            raw = {"invoke_model": data}
            text = self._extract_text(data)

        try:
            out = json.loads(text)
        except Exception:
            # Sometimes models wrap JSON in code fences or stray text; attempt a best-effort strip.
            cleaned = text.strip()
            cleaned = cleaned.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            out = json.loads(cleaned)

        return BedrockResult(
            summary_markdown=out.get("summary_markdown") or "",
            tags=out.get("tags") or [],
            raw={"model_response": raw, "parsed": out},
        )

    def _build_body(self, prompt: str, max_tokens: int) -> Dict[str, Any]:
        # Anthropic messages API format
        if self.model_id.startswith("anthropic."):
            return {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": max_tokens,
                "temperature": 0.2,
                "messages": [
                    {"role": "user", "content": [{"type": "text", "text": prompt}]},
                ],
            }

        # Amazon Titan Text models
        if self.model_id.startswith("amazon.titan-text"):
            return {
                "inputText": prompt,
                "textGenerationConfig": {
                    "maxTokenCount": max_tokens,
                    "temperature": 0.2,
                    "topP": 0.9,
                },
            }

        # Fallback: try a simple prompt field
        return {"prompt": prompt, "max_tokens": max_tokens, "temperature": 0.2}

    @staticmethod
    def _extract_text(model_response: Dict[str, Any]) -> str:
        # Anthropic returns {"content":[{"type":"text","text":"..."}], ...}
        content = model_response.get("content")
        if isinstance(content, list) and content:
            parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(item.get("text", ""))
            return "".join(parts).strip()
        # Titan returns {"results":[{"outputText":"..."}], ...}
        results = model_response.get("results")
        if isinstance(results, list) and results:
            ot = results[0].get("outputText")
            if ot is not None:
                return str(ot).strip()
        # Fallback
        if "outputText" in model_response:
            return str(model_response["outputText"]).strip()
        return json.dumps(model_response)


