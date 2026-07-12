import os

from flask import current_app, has_app_context

from openai import OpenAI


_client = None


def get_openai_client():
    global _client

    if _client:
        return _client

    api_key = None

    if has_app_context():
        api_key = current_app.config.get("OPENAI_API_KEY")

    api_key = api_key or os.getenv("OPENAI_API_KEY")

    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY not configured"
        )

    _client = OpenAI(api_key=api_key)

    return _client


def upload_file_to_openai(file_path):

    if not os.path.exists(file_path):
        raise FileNotFoundError("File not found")

    with open(file_path, "rb") as file:
        uploaded_file = get_openai_client().files.create(
            file=file,
            purpose="user_data"
        )

    return uploaded_file


def delete_openai_file(file_id):

    try:
        get_openai_client().files.delete(file_id)
    except Exception:
        pass


def analyze_file_with_prompt(file_id, prompt):

    if has_app_context():
        model = current_app.config.get("OPENAI_MODEL")
    else:
        model = None

    model = model or os.getenv("OPENAI_MODEL", "gpt-4.1-mini")

    response = get_openai_client().responses.create(
        model=model,
        input=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_file",
                        "file_id": file_id,
                    },
                    {
                        "type": "input_text",
                        "text": prompt,
                    },
                ],
            }
        ],
    )

    return response.output_text.strip()
