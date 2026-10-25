"""The only path to a language model (D-03). Everything else calls this package."""

from ragnarok.llm_gateway.client import ChatResult, Gateway, OllamaGateway, Priority

__all__ = ["ChatResult", "Gateway", "OllamaGateway", "Priority"]
