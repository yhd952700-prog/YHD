"""Provider configuration for LiuHao AI OS.

This module provides configuration and instantiation of AI providers.
Supports both MockProvider for development and real providers
(OpenAI, Anthropic, Google, Ollama, Moonshot, DeepSeek) for production use.
Also integrates with external frameworks: AutoGen, AG2, LangGraph.

Usage:
    from ai.providers import get_provider
    provider = get_provider()  # Auto-detects from env config
"""

import os
from typing import Optional, Dict, Any

# Provider type enumeration
class ProviderType:
    MOCK = "mock"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    OLLAMA = "ollama"
    MOONSHOT = "moonshot"
    DEEPSEEK = "deepseek"

# External framework integration flags
class Framework:
    AUTOgen = "autogen"
    AG2 = "ag2"
    LANGGRAPH = "langgraph"

# Base provider class
class BaseProvider:
    """Base class for all provider implementations."""
    
    def __init__(self, name: str, model: str, api_key: Optional[str] = None):
        self.name = name
        self.model = model
        self.api_key = api_key or os.environ.get("AI_PROVIDER_KEY", "[REDACTED]")
    
    def generate(self, prompt: str, **kwargs) -> str:
        """Generate response to prompt. To be implemented by subclasses."""
        raise NotImplementedError
    
    def get_capabilities(self) -> Dict[str, Any]:
        """Return provider capabilities metadata."""
        return {
            "name": self.name,
            "model": self.model,
            "type": self.__class__.__name__,
        }

# MockProvider for development and testing
class MockProvider(BaseProvider):
    """Mock provider for development, testing, and local operations."""
    
    def generate(self, prompt: str, **kwargs) -> str:
        """Return a mock response based on prompt content."""
        # Simple mock response generation
        if "hello" in prompt.lower() or "hi" in prompt.lower():
            return "Hello! I'm LiuHao AI, how can I assist you today?"
        elif "status" in prompt.lower():
            return f"LiuHao AI OS status: {self.get_capabilities()}"
        elif "help" in prompt.lower():
            return "I can help you with: coding, analysis, creative writing, and more. " \
                   "Ask me about AI providers, frameworks, or project status."
        else:
            return f"Mock response to: {prompt[:80]}..."

# OpenAI provider
class OpenAIProvider(BaseProvider):
    """OpenAI API provider integration."""
    
    def generate(self, prompt: str, **kwargs) -> str:
        """Generate response via OpenAI API."""
        import openai
        
        model = kwargs.get("model", self.model)
        temperature = kwargs.get("temperature", 0.7)
        
        try:
            response = openai.ChatCompletion.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            return f"OpenAI error: {str(e)}"

# Anthropic provider
class AnthropicProvider(BaseProvider):
    """Anthropic API provider integration."""
    
    def generate(self, prompt: str, **kwargs) -> str:
        """Generate response via Anthropic API."""
        import anthropic
        
        model = kwargs.get("model", self.model)
        temperature = kwargs.get("temperature", 0.7)
        
        try:
            client = anthropic.Anthropic(api_key=self.api_key)
            response = client.messages.create(
                model=model,
                max_tokens=1000,
                temperature=temperature,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.content[0].text
        except Exception as e:
            return f"Anthropic error: {str(e)}"

# Google provider
class GoogleProvider(BaseProvider):
    """Google Gemini API provider integration."""
    
    def generate(self, prompt: str, **kwargs) -> str:
        """Generate response via Google Gemini API."""
        import google.generativeai as genai
        
        model = kwargs.get("model", self.model)
        
        try:
            genai.configure(api_key=self.api_key)
            model_obj = genai.GenerativeModel(model)
            response = model_obj.generate_content(prompt)
            return response.text
        except Exception as e:
            return f"Google error: {str(e)}"

# Ollama provider (self-hosted)
class OllamaProvider(BaseProvider):
    """Ollama self-hosted model provider integration."""
    
    def generate(self, prompt: str, **kwargs) -> str:
        """Generate response via Ollama local API."""
        import requests
        
        model = kwargs.get("model", self.model)
        base_url = kwargs.get("base_url", os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"))
        
        try:
            response = requests.post(
                f"{base_url}/api/generate",
                json={"model": model, "prompt": prompt},
                timeout=30,
            )
            if response.status_code == 200:
                result = response.json()
                return result.get("response", str(result))
            else:
                return f"Ollama error {response.status_code}: {response.text}"
        except Exception as e:
            return f"Ollama connection error: {str(e)}"

# Moonshot provider
class MoonshotProvider(BaseProvider):
    """Moonshot AI provider integration."""
    
    def generate(self, prompt: str, **kwargs) -> str:
        """Generate response via Moonshot API."""
        import requests
        
        model = kwargs.get("model", self.model)
        base_url = kwargs.get("base_url", os.environ.get("MOONSHOT_BASE_URL", "https://api.moonshot.ai/v1"))
        api_key = self.api_key or os.environ.get("MOONSHOT_API_KEY", "[REDACTED]")
        
        try:
            headers = {"Authorization": f"Bearer {api_key}"} if api_key != "[REDACTED]" else {}
            response = requests.post(
                f"{base_url}/chat/completions",
                json={"model": model, "messages": [{"role": "user", "content": prompt}]},
                headers=headers,
                timeout=30,
            )
            if response.status_code == 200:
                result = response.json()
                return result.get("choices", [{}])[0].get("message", {}).get("content", str(result))
            else:
                return f"Moonshot error {response.status_code}: {response.text}"
        except Exception as e:
            return f"Moonshot connection error: {str(e)}"

# DeepSeek provider
class DeepSeekProvider(BaseProvider):
    """DeepSeek API provider integration."""
    
    def generate(self, prompt: str, **kwargs) -> str:
        """Generate response via DeepSeek API."""
        import requests
        
        model = kwargs.get("model", self.model)
        base_url = kwargs.get("base_url", os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"))
        api_key = self.api_key or os.environ.get("DEEPSEEK_API_KEY", "[REDACTED]")
        
        try:
            headers = {"Authorization": f"Bearer {api_key}"} if api_key != "[REDACTED]" else {}
            response = requests.post(
                f"{base_url}/chat/completions",
                json={"model": model, "messages": [{"role": "user", "content": prompt}]},
                headers=headers,
                timeout=30,
            )
            if response.status_code == 200:
                result = response.json()
                return result.get("choices", [{}])[0].get("message", {}).get("content", str(result))
            else:
                return f"DeepSeek error {response.status_code}: {response.text}"
        except Exception as e:
            return f"DeepSeek connection error: {str(e)}"

# AutoGen integrator
class AutoGenIntegrator:
    """Integration layer for AutoGen framework."""
    
    def __init__(self, provider: BaseProvider):
        self.provider = provider
    
    def create_agent(self, name: str, system_prompt: str) -> Dict[str, Any]:
        """Create an AutoGen-compatible agent configuration."""
        return {
            "name": name,
            "system_prompt": system_prompt,
            "model": self.provider.model,
            "api_key": self.provider.api_key,
            "provider": self.provider.name,
        }
    
    def generate_response(self, messages: list, **kwargs) -> str:
        """Generate response using provider through AutoGen pipeline."""
        # Last message in conversation
        last_msg = messages[-1]["content"] if messages else ""
        return self.provider.generate(last_msg, **kwargs)

# AG2 integrator
class AG2Integrator:
    """Integration layer for AG2 (Autogen 2.0) framework."""
    
    def __init__(self, provider: BaseProvider):
        self.provider = provider
    
    def create_agent_profile(self, name: str, instructions: str) -> Dict[str, Any]:
        """Create an AG2 agent profile."""
        return {
            "name": name,
            "instructions": instructions,
            "model": self.provider.model,
            "provider": self.provider.name,
            "capabilities": self.provider.get_capabilities(),
        }
    
    def dispatch_task(self, agent_name: str, task: str, **kwargs) -> str:
        """Dispatch a task to an AG2 agent."""
        return self.provider.generate(
            f"Agent {agent_name} executing task: {task}",
            **kwargs
        )

# LangGraph integrator
class LangGraphIntegrator:
    """Integration layer for LangGraph framework."""
    
    def __init__(self, provider: BaseProvider):
        self.provider = provider
    
    def add_node(self, name: str, func) -> None:
        """Add a LangGraph node using provider generation."""
        def wrapped(**state):
            return {"output": self.provider.generate(str(func(state)))}
        # In real usage, this would be added to a StateGraph
        return wrapped
    
    def create_edge(self, source: str, target: str, condition: str = None) -> Dict[str, Any]:
        """Create a LangGraph edge configuration."""
        return {
            "source": source,
            "target": target,
            "condition": condition or "always",
        }

# Provider factory and registry
class ProviderFactory:
    """Factory class for creating provider instances."""
    
    _providers: Dict[str, type] = {
        ProviderType.MOCK: MockProvider,
        ProviderType.OPENAI: OpenAIProvider,
        ProviderType.ANTHROPIC: AnthropicProvider,
        ProviderType.GOOGLE: GoogleProvider,
        ProviderType.OLLAMA: OllamaProvider,
        ProviderType.MOONSHOT: MoonshotProvider,
        ProviderType.DEEPSEEK: DeepSeekProvider,
    }
    
    _integrators: Dict[str, type] = {
        Framework.AUTOgen: AutoGenIntegrator,
        Framework.AG2: AG2Integrator,
        Framework.LANGGRAPH: LangGraphIntegrator,
    }
    
    @classmethod
    def create_provider(cls, provider_type: str = None, **kwargs) -> BaseProvider:
        """Create a provider instance by type."""
        if provider_type is None:
            provider_type = os.environ.get("AI_PROVIDER_TYPE", ProviderType.MOCK)
        
        provider_class = cls._providers.get(provider_type)
        if not provider_class:
            raise ValueError(f"Unknown provider type: {provider_type}")
        
        return provider_class(**kwargs)
    
    @classmethod
    def create_integrator(
        cls, 
        framework: str, 
        provider: BaseProvider
    ) -> Any:
        """Create an integrator for the specified framework."""
        integrator_class = cls._integrators.get(framework)
        if not integrator_class:
            raise ValueError(f"Unknown framework: {framework}")
        return integrator_class(provider)
    
    @classmethod
    def get_supported_providers(cls) -> list:
        """Return list of supported provider types."""
        return list(cls._providers.keys())
    
    @classmethod
    def get_supported_frameworks(cls) -> list:
        """Return list of supported external frameworks."""
        return list(cls._integrators.keys())

# Global provider instance
_provider_instance: BaseProvider = None

def get_provider() -> BaseProvider:
    """Get the global provider instance, auto-detecting from environment.

    The provider type is determined by the AI_PROVIDER_TYPE environment variable.
    Set this to a valid ProviderType (openai, anthropic, google, ollama,
    moonshot, deepseek) to use a real provider, or MOCK for development.

    Example:
        export AI_PROVIDER_TYPE=openai
        export AI_PROVIDER_KEY=[REDACTED]
    """
    global _provider_instance

    if _provider_instance is None:
        # Get provider type from env var, default to openai
        provider_type = os.environ.get("AI_PROVIDER_TYPE", "openai").lower()

        # Validate provider type is supported
        if provider_type not in ProviderFactory._providers:
            print(f"Warning: Unknown provider type '{provider_type}', defaulting to MOCK")
            provider_type = "mock"

        # Required args for all providers: name and model
        # Use sensible defaults; override with env vars if needed
        name = os.environ.get("AI_PROVIDER_NAME", "liuhao-assistant")
        model = os.environ.get("AI_PROVIDER_MODEL", "[REDACTED]")
        api_key = os.environ.get("AI_PROVIDER_KEY", "[REDACTED]")

        _provider_instance = ProviderFactory.create_provider(
            provider_type,
            name=name,
            model=model,
            api_key=api_key,
        )

        # Log provider info (masked)
        print(f"Provider initialized: {provider_type} (name={name}, model={model}, key: {api_key[:8]}...)")
    
    return _provider_instance

def set_provider(provider: BaseProvider) -> None:
    """Set the global provider instance explicitly."""
    global _provider_instance
    _provider_instance = provider

def reset_provider() -> None:
    """Reset the global provider instance."""
    global _provider_instance
    _provider_instance = None

# Provider type detection helper
def detect_provider_type_from_env() -> str:
    """Detect and return provider type from environment variables."""
    provider_type = os.environ.get("AI_PROVIDER_TYPE", ProviderType.MOCK)
    return provider_type

# Convenience functions for external framework usage
def with_autogen(func):
    """Decorator to wrap functions for AutoGen integration."""
    def wrapper(*args, **kwargs):
        provider = get_provider()
        integrator = ProviderFactory.create_integrator(Framework.AUTOgen, provider)
        return integrator.generate_response(kwargs.get("messages", []), **kwargs)
    return wrapper

def with_ag2(func):
    """Decorator to wrap functions for AG2 integration."""
    def wrapper(*args, **kwargs):
        provider = get_provider()
        integrator = ProviderFactory.create_integrator(Framework.AG2, provider)
        return integrator.dispatch_task(kwargs.get("agent", "default"), str(kwargs.get("task", "")), **kwargs)
    return wrapper

def with_langgraph(func):
    """Decorator to wrap functions for LangGraph integration."""
    def wrapper(*args, **kwargs):
        provider = get_provider()
        integrator = ProviderFactory.create_integrator(Framework.LANGGRAPH, provider)
        return integrator.add_node(kwargs.get("node_name", "default"), func)
    return wrapper
