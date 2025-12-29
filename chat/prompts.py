"""Prompt templates for Bedrock chat API."""

from typing import List, Dict


def build_system_prompt(evidence_passages: List[Dict]) -> str:
    """
    Build a system prompt for Bedrock that includes evidence passages.
    
    Args:
        evidence_passages: List of passage dicts with 'snippet', 'full_text', 'document_id', 'page_number', 'url', 'title', 'source'
    
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
            url = passage.get("url")  # Web article URL
            title = passage.get("title")  # Web article title
            source = passage.get("source")  # Web article source
            
            if url:  # Web article
                evidence_text += f"[{i}] News Article: {title or 'Untitled'}\n"
                evidence_text += f"Source: {source or 'Unknown'}\n"
                evidence_text += f"URL: {url}\n"
            else:  # Local document
                evidence_text += f"[{i}] Document {doc_id}, Page {page_num}:\n"
            
            evidence_text += f"{full_text}\n\n"
    
    prompt = f"""You are a helpful assistant answering questions about Jeffrey Epstein using both archived documents and recent news articles.

Your task is to answer the user's question using ONLY the evidence passages provided below. When citing sources:
- For archived documents, reference as [1], [2], etc.
- For news articles, include the article title and source in your citations
- If the evidence is insufficient, clearly state that

## Instructions:
1. Answer the question using information from the evidence passages
2. When citing information, reference the passage number like [1], [2], etc.
3. For news articles, mention the source (e.g., "According to [1] from The New York Times...")
4. If the evidence doesn't contain enough information to answer, say so explicitly
5. Be factual and precise - do not make up information not in the evidence
6. Write your answer in clear, natural language - use plain text, not markdown or code formatting
{evidence_text}"""
    
    return prompt

