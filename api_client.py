from anthropic import Anthropic
from openai import OpenAI
from config import Config


def call_claude(system_prompt: str, user_prompt: str, config: Config) -> str:
    if config.api.provider == "openai":
        return _call_openai(system_prompt, user_prompt, config)
    return _call_anthropic(system_prompt, user_prompt, config)


def _call_anthropic(system_prompt: str, user_prompt: str, config: Config) -> str:
    client = Anthropic(api_key=config.api.api_key)
    response = client.messages.create(
        model=config.api.model,
        max_tokens=4096,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return response.content[0].text


def _call_openai(system_prompt: str, user_prompt: str, config: Config) -> str:
    client = OpenAI(api_key=config.api.api_key)
    response = client.chat.completions.create(
        model=config.api.model,
        max_tokens=4096,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    return response.choices[0].message.content
