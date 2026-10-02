from typing import Literal, Optional
import os
from focus.utils.log import err, warn


class BaseAgent:
    def __init__(
        self, model: str, api_key: str, provider: str = Literal["openai", "anthropic"]
    ):
        self.model = model
        self.provider = provider
        self.api_key = api_key

    def capture_screen(self, model):
        pass
