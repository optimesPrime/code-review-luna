from openai import OpenAI
from config import Config


def call_claude(system_prompt: str, user_prompt: str, config: Config) -> str:
    kwargs = {"api_key": config.api.api_key}
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
