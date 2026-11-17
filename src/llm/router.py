# pip install openai   (the HTTP client package; every request goes to Rapid Router)
import os
from pathlib import Path

from openai import OpenAI

BASE_URL = "https://rapidrouter.ai/api/v1"


def _key_from_dotenv() -> str | None:
    for candidate in (Path(".env"), Path(__file__).resolve().parent / ".env"):
        if not candidate.is_file():
            continue
        for line in candidate.read_text(encoding="utf-8").splitlines():
            name, sep, value = line.strip().removeprefix("export ").partition("=")
            if sep and name.strip() == "RAPIDROUTER_API_KEY":
                return value.strip().strip("'\"")
    return None


def get_client() -> OpenAI:
    api_key = os.environ.get("RAPIDROUTER_API_KEY") or _key_from_dotenv()
    if not api_key:
        raise KeyError("RAPIDROUTER_API_KEY (set it in the environment or in src/.env)")
    return OpenAI(base_url=BASE_URL, api_key=api_key)


def generate(prompt: str, model: str, temperature: float = 0.0, max_tokens: int = 1024) -> str:
    client = get_client()
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return response.choices[0].message.content or ""


def list_models() -> list[str]:
    client = get_client()
    return sorted(item.id for item in client.models.list().data)


if __name__ == "__main__":
    import sys

    try:
        models = list_models()
    except KeyError:
        sys.exit("export RAPIDROUTER_API_KEY first")
    print(f"{len(models)} models available")
    for name in models:
        print(" ", name)
    if "--hello" in sys.argv:
        print(generate("Say hello in one short sentence.", models[0]))
