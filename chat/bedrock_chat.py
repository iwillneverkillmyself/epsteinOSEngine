"""Bedrock chat integration for grounded responses."""

from typing import List, Dict, Optional
import logging
import boto3
import json

logger = logging.getLogger(__name__)


class BedrockChatClient:
    """Client for Bedrock Converse API for chat responses."""
    
    def __init__(self, region: Optional[str] = None, model_id: Optional[str] = None):
        self.region = region or "us-east-1"
        # Use a chat-optimized model
        self.model_id = model_id or "amazon.nova-pro-v1:0"
        self._client = boto3.client("bedrock-runtime", region_name=self.region)
    
    def converse(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        max_tokens: int = 2000,
        temperature: float = 0.2,
    ) -> Dict[str, any]:
        """
        Call Bedrock Converse API with messages.
        
        Args:
            messages: List of {role: "user"|"assistant", content: str}
            system_prompt: Optional system prompt
            max_tokens: Maximum tokens in response
            temperature: Sampling temperature
        
        Returns:
            Dict with:
            - answer_markdown: str (the model's response)
            - raw: Dict (raw API response)
        """
        try:
            # Convert messages to Bedrock format
            bedrock_messages = []
            for msg in messages:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                bedrock_messages.append({
                    "role": role,
                    "content": [{"text": content}]
                })
            
            # Build request
            request_params = {
                "modelId": self.model_id,
                "messages": bedrock_messages,
                "inferenceConfig": {
                    "maxTokens": max_tokens,
                    "temperature": temperature,
                    "topP": 0.9,
                }
            }
            
            if system_prompt:
                request_params["system"] = [{"text": system_prompt}]
            
            # Call API
            response = self._client.converse(**request_params)
            
            # Extract text from response
            answer_text = ""
            try:
                # Try multiple response structures
                if "output" in response:
                    output = response["output"]
                    if "message" in output:
                        message = output["message"]
                        if "content" in message:
                            content = message["content"]
                            if isinstance(content, list) and content:
                                for item in content:
                                    if isinstance(item, dict) and "text" in item:
                                        answer_text = item.get("text", "")
                                        break
                            elif isinstance(content, str):
                                answer_text = content
                
                # Fallback: try direct text field
                if not answer_text and "text" in response:
                    answer_text = response["text"]
                
                # Log if still empty
                if not answer_text:
                    logger.warning(f"Empty Bedrock response. Response structure: {list(response.keys()) if isinstance(response, dict) else type(response)}")
                    logger.debug(f"Full response: {response}")
            except Exception as e:
                logger.error(f"Error extracting text from Bedrock response: {e}", exc_info=True)
                answer_text = ""
            
            return {
                "answer_markdown": answer_text,
                "raw": response,
            }
            
        except Exception as e:
            logger.error(f"Bedrock converse error: {e}")
            raise


def generate_answer(
    user_question: str,
    evidence_passages: List[Dict],
    conversation_history: Optional[List[Dict[str, str]]] = None,
    max_tokens: int = 2000,
) -> Dict[str, any]:
    """
    Generate a grounded answer using Bedrock.
    
    Args:
        user_question: The user's question
        evidence_passages: List of passage dicts with evidence
        conversation_history: Optional list of previous messages
        max_tokens: Maximum tokens in response
    
    Returns:
        Dict with answer_markdown and raw response
    """
    from chat.prompts import build_system_prompt
    
    # Build system prompt with evidence (instructions + passages, no user question)
    system_prompt = build_system_prompt(evidence_passages)
    
    # Build messages
    messages = []
    if conversation_history:
        # Add history (last N messages, excluding the current question)
        messages.extend(conversation_history[-10:])  # Limit to last 10 messages
    
    # Add current question
    messages.append({
        "role": "user",
        "content": user_question
    })
    
    # Call Bedrock
    client = BedrockChatClient()
    result = client.converse(
        messages=messages,
        system_prompt=system_prompt,
        max_tokens=max_tokens,
    )
    
    return result

