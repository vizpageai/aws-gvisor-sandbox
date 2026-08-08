from __future__ import annotations

import os

from openai import OpenAI

base_url = os.environ.get("GPT_OSS_BASE_URL", "http://127.0.0.1:8000/v1")

client = OpenAI(
    base_url=base_url,
    api_key=os.environ.get("GPT_OSS_API_KEY", "EMPTY"),
)

response = client.chat.completions.create(
    model="openai/gpt-oss-20b",
    messages=[
        {"role": "system", "content": "You are a concise assistant."},
        {"role": "user", "content": "Explain MXFP4 quantization in two sentences."},
    ],
    temperature=0.2,
    max_tokens=200,
)

print(response.choices[0].message.content)
