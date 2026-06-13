from anthropic import Anthropic
from openai import OpenAI
from config import Config


def call_claude(system_prompt: str, user_prompt: str, config: Config) -> str:
    # 中转站：base_url 已设置，统一走 OpenAI 兼容接口
    if config.api.base_url:
        return _call_openai(system_prompt, user_prompt, config)
    # 直连 Anthropic 官方
    if config.api.provider == "anthropic":
        return _call_anthropic(system_prompt, user_prompt, config)
    # 直连 OpenAI 官方（或其他 OpenAI 兼容服务）
    return _call_openai(system_prompt, user_prompt, config)


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
    kwargs: dict = {"api_key": config.api.api_key}
    if config.api.base_url:
        kwargs["base_url"] = config.api.base_url
    client = OpenAI(**kwargs)
    response = client.chat.completions.create(
        model=config.api.model,
        max_tokens=4096,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    # 部分非标准代理端点直接返回字符串而非 SDK Response 对象
    if isinstance(response, str):
        return response
    return response.choices[0].message.content
