from __future__ import annotations

import os
import sys

from openai import OpenAI

BASE_URL = os.getenv("OPENAI_BASE_URL", "http://127.0.0.1:8000/v1")
MODEL = os.getenv("OPENAI_MODEL", "openai/gpt-oss-20b")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    client = OpenAI(base_url=BASE_URL, api_key=os.getenv("OPENAI_API_KEY", "EMPTY"))

    models = client.models.list()
    print("Available models:", [model.id for model in models.data])

    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": "You are a concise assistant."},
            {"role": "user", "content": "What GPU are you running on? Answer briefly."},
        ],
        reasoning_effort="low",
        temperature=0.2,
        max_tokens=400,
    )

    print("Response:", response.choices[0].message.content)
    print("Usage:", response.usage)


if __name__ == "__main__":
    main()
