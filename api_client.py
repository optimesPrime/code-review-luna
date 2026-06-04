from anthropic import Anthropic
from config import Config


def call_claude(system_prompt: str, user_prompt: str, config: Config) -> str:
    client = Anthropic(api_key=config.api.api_key)
    response = client.messages.create(
        model=config.api.model,
        max_tokens=4096,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return response.content[0].text
