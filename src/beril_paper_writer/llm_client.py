"""llm_client.py — Native Python LLM client with zero dependencies.

Implements stateless JSON extraction calls to CBORG, Anthropic, and OpenAI.
Strips markdown code fences from CBORG responses to fix known translation bugs.

STATUS (Stage 1 Tier E, 2026-05-11): **forward-deployed / not currently
called by orchestrator.py.** Every LLM operation in the active pipeline
goes through `claude -p` subprocess (Claude Code CLI). This module
exists as a multi-provider abstraction for a future M2.x phase that
may want CBORG cost efficiency (local-LBL endpoint) without going
through Claude Code. Decision deferred per STAGED_IMPROVEMENT_PLAN.md
Stage 1 Tier E: keep the code, don't invest further until a consumer
emerges. The `parse_xml_files` helper at the bottom is also unused
currently.
"""

import json
import re
import urllib.request
import urllib.error

from beril_paper_writer.config import config

class LLMInvocationError(Exception):
    pass

def strip_json_fences(text: str) -> str:
    """Strip ```json ... ``` markdown fences sometimes emitted by CBORG."""
    text = text.strip()
    if text.startswith("```"):
        # Match ```json\n(content)\n```
        match = re.search(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1)
    return text

def invoke_stateless_json(prompt: str, provider: str = "auto", model: str = None) -> dict:
    """Hit the appropriate REST API and return the parsed JSON."""
    
    if provider == "auto":
        provider = config.default_stateless_provider
        
    if provider == "none":
        raise LLMInvocationError("No API keys found for CBORG, Anthropic, or OpenAI.")
        
    if provider == "cborg":
        # Default to a fast/cheap model for stateless extraction if not specified
        model_name = model or "lbl/cborg-chat"
        url = "https://api.cborg.lbl.gov/v1/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.cborg_api_key}"
        }
        # OpenAI compatible schema
        data = {
            "model": model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
            "response_format": {"type": "json_object"}
        }
        
    elif provider == "anthropic":
        model_name = model or "claude-3-5-haiku-20241022"
        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "Content-Type": "application/json",
            "x-api-key": config.anthropic_api_key,
            "anthropic-version": "2023-06-01"
        }
        data = {
            "model": model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
            "max_tokens": 4096,
            "system": "You must respond with valid JSON only. Do not wrap it in markdown fences."
        }
        
    elif provider == "openai":
        model_name = model or "gpt-4o-mini"
        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.openai_api_key}"
        }
        data = {
            "model": model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
            "response_format": {"type": "json_object"}
        }
    else:
        raise ValueError(f"Unknown provider: {provider}")

    req = urllib.request.Request(url, data=json.dumps(data).encode("utf-8"), headers=headers)
    
    try:
        with urllib.request.urlopen(req) as response:
            resp_bytes = response.read()
            resp_dict = json.loads(resp_bytes.decode("utf-8"))
            
            # Extract content
            if provider in ("cborg", "openai"):
                content = resp_dict["choices"][0]["message"]["content"]
            elif provider == "anthropic":
                content = resp_dict["content"][0]["text"]
                
            clean_content = strip_json_fences(content)
            
            try:
                return json.loads(clean_content)
            except json.JSONDecodeError:
                raise LLMInvocationError(f"Model did not return valid JSON: {clean_content}")
                
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        raise LLMInvocationError(f"HTTP {e.code}: {error_body}")
    except urllib.error.URLError as e:
        raise LLMInvocationError(f"Network error: {e.reason}")

def invoke_stateless_text(prompt: str, system_prompt: str = "", provider: str = "auto", model: str = None) -> str:
    """Hit the appropriate REST API and return raw text. Supports system prompts."""
    
    if provider == "auto":
        provider = config.default_stateless_provider
        
    if provider == "none":
        raise LLMInvocationError("No API keys found for CBORG, Anthropic, or OpenAI.")
        
    if provider == "cborg":
        # Default to a highly capable model for drafting
        model_name = model or "lbl/cborg-coder"
        url = "https://api.cborg.lbl.gov/v1/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.cborg_api_key}"
        }
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        
        data = {
            "model": model_name,
            "messages": messages,
            "temperature": 0.0,
        }
        
    elif provider == "anthropic":
        model_name = model or "claude-3-5-sonnet-20241022"
        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "Content-Type": "application/json",
            "x-api-key": config.anthropic_api_key,
            "anthropic-version": "2023-06-01"
        }
        data = {
            "model": model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
            "max_tokens": 8192,
        }
        if system_prompt:
            data["system"] = system_prompt
            
    elif provider == "openai":
        model_name = model or "gpt-4o"
        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.openai_api_key}"
        }
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        
        data = {
            "model": model_name,
            "messages": messages,
            "temperature": 0.0,
        }
    else:
        raise ValueError(f"Unknown provider: {provider}")

    req = urllib.request.Request(url, data=json.dumps(data).encode("utf-8"), headers=headers)
    
    try:
        with urllib.request.urlopen(req) as response:
            resp_bytes = response.read()
            resp_dict = json.loads(resp_bytes.decode("utf-8"))
            
            # Extract content
            if provider in ("cborg", "openai"):
                return resp_dict["choices"][0]["message"]["content"]
            elif provider == "anthropic":
                return resp_dict["content"][0]["text"]
                
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        raise LLMInvocationError(f"HTTP {e.code}: {error_body}")
    except urllib.error.URLError as e:
        raise LLMInvocationError(f"Network error: {e.reason}")

def parse_xml_files(text: str) -> dict:
    """Extract <file path="...">content</file> blocks from a string.
    Returns a dict mapping file path strings to their content strings.
    """
    files = {}
    pattern = r'<file\s+path="([^"]+)">\s*(.*?)\s*</file>'
    matches = re.finditer(pattern, text, re.DOTALL | re.IGNORECASE)
    for match in matches:
        path = match.group(1)
        content = match.group(2)
        files[path] = content
    return files
