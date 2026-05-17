"""config.py — Configuration and .env parser.

STATUS (audit 2026-05-17): only the `haiku_model` attribute is read
by orchestrator.phase_review for Tier-2 model selection. The
multi-provider API-key discovery (CBORG / Anthropic / OpenAI) is
not used by the active pipeline — `claude -p` subprocesses inherit
their own auth via Claude Code. The companion `llm_client.py`
forward-deployed alongside this module has been deleted (audit
2026-05-17, item 2). Once the dust settles on the architecture this
module could shrink to just the `haiku_model` env lookup; deferred
for now.
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
