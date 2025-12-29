"""Prompt templates for Bedrock chat API."""

from typing import List, Dict


def build_system_prompt(evidence_passages: List[Dict]) -> str:
    """
    Build a system prompt for Bedrock that includes evidence passages.
    
    Args:
        evidence_passages: List of passage dicts with 'snippet', 'full_text', 'document_id', 'page_number'
    
    Returns:
        Formatted system prompt string
    """
    # Format evidence passages
    evidence_text = ""
    if evidence_passages:
        evidence_text = "\n\n## Evidence Passages:\n\n"
        for i, passage in enumerate(evidence_passages, 1):
            snippet = passage.get("snippet", "")
            full_text = passage.get("full_text", snippet)
            doc_id = passage.get("document_id", "unknown")
            page_num = passage.get("page_number", 0)
            
            evidence_text += f"[{i}] Document {doc_id}, Page {page_num}:\n"
            evidence_text += f"{full_text}\n\n"
    
    prompt = f"""You are a helpful assistant answering questions about documents in the Epstein Files archive.

Your task is to answer the user's question using ONLY the evidence passages provided below. If the evidence is insufficient, clearly state that and suggest better search terms.

## Instructions:
1. Answer the question using information from the evidence passages
2. When citing information, reference the passage number like [1], [2], etc.
3. If the evidence doesn't contain enough information to answer, say so explicitly
4. If you cannot answer from the evidence, suggest alternative search terms or questions
5. Be factual and precise - do not make up information not in the evidence
6. Write your answer in clear, natural language - use plain text, not markdown or code formatting
{evidence_text}"""
    
    return prompt

