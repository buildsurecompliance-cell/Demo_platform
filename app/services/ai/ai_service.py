import os

from openai import OpenAI


client = OpenAI()


def upload_file_to_openai(file_path):

    if not os.path.exists(file_path):
        raise FileNotFoundError("File not found")

    with open(file_path, "rb") as file:
        uploaded_file = client.files.create(
            file=file,
            purpose="user_data"
        )

    return uploaded_file


def delete_openai_file(file_id):

    try:
        client.files.delete(file_id)
    except Exception:
        pass


def analyze_file_with_prompt(file_id, prompt):

    model = os.getenv(
        "OPENAI_MODEL",
        "gpt-4.1-mini"
    )

    response = client.responses.create(
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