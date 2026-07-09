import json
import os

from app.services.ai.ai_service import (
    analyze_file_with_prompt,
    delete_openai_file,
    upload_file_to_openai,
)

from app.services.document_intelligence.prompts import (
    get_prompt_for_document,
)


def parse_document_with_ai(
    file_path,
    category,
):

    prompt = get_prompt_for_document(
        category
    )

    if not prompt:
        return {
            "success": False,
            "error": f"No prompt found for category: {category}",
            "data": None,
            "raw": None,
        }

    uploaded_file = None

    try:
        uploaded_file = upload_file_to_openai(
            file_path
        )

        raw_result = analyze_file_with_prompt(
            uploaded_file.id,
            prompt
        )

        data = json.loads(
            raw_result
        )

        return {
            "success": True,
            "error": None,
            "data": data,
            "raw": raw_result,
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "data": None,
            "raw": None,
        }

    finally:
        if uploaded_file:
            delete_openai_file(
                uploaded_file.id
            )