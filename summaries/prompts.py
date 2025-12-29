"""Prompt templates for Bedrock summarization + tagging."""

from __future__ import annotations

from typing import List

PROMPT_VERSION = "v1"


DEFAULT_TAG_TAXONOMY: List[str] = [
    "financial",
    "legal",
    "government",
    "email",
    "travel",
    "contacts",
    "medical",
    "business",
    "press",
    "investigation",
    "evidence",
    "other",
]


def build_summary_and_tags_prompt(document_text: str, taxonomy: List[str] | None = None) -> str:
    taxonomy = taxonomy or DEFAULT_TAG_TAXONOMY
    taxonomy_str = ", ".join(taxonomy)
    return f"""
You are a careful analyst. Summarize the document text and assign tags.

Requirements:
- Output MUST be valid JSON only (no markdown, no extra text).
- Summary must be concise but useful for browsing.
- Tags must be chosen ONLY from this taxonomy: [{taxonomy_str}]
- Multi-label: include all that apply. If unsure, omit. If none apply, use ["other"].

Return JSON with this exact schema:
{{
  "summary_markdown": "string (markdown allowed inside this field)",
  "tags": [
    {{"id": "taxonomy_tag_id", "confidence": 0.0}}
  ]
}}

Document text:
\"\"\"{document_text}\"\"\"
""".strip()


