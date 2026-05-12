"""config.py — Configuration and .env parser.

Loads API keys and discovers the active LLM provider based on available keys.
Prioritizes CBORG for cost efficiency if available, falling back to Anthropic
or OpenAI.

STATUS (Stage 1 Tier E, 2026-05-11): the `haiku_model` attribute is
read by orchestrator.phase_review for Tier 2 model selection.
Otherwise this module's purpose (multi-provider API-key discovery)
is unused by the active pipeline (claude -p subprocess inherits its
own auth). Companion to `llm_client.py`; both kept as
forward-deployed per STAGED_IMPROVEMENT_PLAN.md Stage 1 Tier E.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

def load_environment():
    """Discover and load the appropriate .env file."""
    search_paths = [
        Path(os.environ.get("BERIL_ROOT", "")),
        Path.home() / "BERIL-research-observatory",
        Path.home() / ".beril",
        Path.cwd(),
        Path.cwd().parent / "beril-extended"
    ]
    
    for path in search_paths:
        if not path.name:
            continue
        env_file = path / ".env"
        if env_file.exists():
            load_dotenv(dotenv_path=env_file)
            break

class Config:
    def __init__(self):
        load_environment()
        
        self.cborg_api_key = os.environ.get("CBORG_API_KEY")
        self.anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY")
        self.openai_api_key = os.environ.get("OPENAI_API_KEY")
        self.haiku_model = os.environ.get("HAIKU_MODEL", "claude-3-haiku-20240307")
        
    @property
    def default_stateless_provider(self) -> str:
        """Determines the cheapest/best provider for pure JSON extraction."""
        if self.cborg_api_key:
            return "cborg"
        if self.anthropic_api_key:
            return "anthropic"
        if self.openai_api_key:
            return "openai"
        return "none"

# Singleton instance
config = Config()
