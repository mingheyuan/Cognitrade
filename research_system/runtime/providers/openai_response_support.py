from __future__ import annotations

import json
from typing import Any


class OpenAIResponseSupport:
    def __init__(self, host) -> None:
        self.host = host

    def should_continue_response(self, response: Any) -> bool:
        return self.response_status(response) == "incomplete" and self.incomplete_reason(response) == "max_output_tokens"

    def response_status(self, response: Any) -> str | None:
        status = getattr(response, "status", None)
        return status if isinstance(status, str) else None

    def response_id(self, response: Any) -> str | None:
        response_id = getattr(response, "id", None)
        return response_id if isinstance(response_id, str) and response_id else None

    def incomplete_reason(self, response: Any) -> str | None:
        details = getattr(response, "incomplete_details", None)
        reason = getattr(details, "reason", None)
        return reason if isinstance(reason, str) else None

    def coerce_responses_text(self, response: Any) -> str:
        output_text = getattr(response, "output_text", None)
        if isinstance(output_text, str):
            if self.looks_like_html(output_text):
                raise self.host.structured_output_error("gateway returned HTML instead of model output")
            return output_text

        if isinstance(response, str):
            if self.looks_like_html(response):
                raise self.host.structured_output_error("gateway returned HTML instead of model output")
            return response

        if isinstance(response, dict):
            output_text = response.get("output_text")
            if isinstance(output_text, str):
                return output_text

        dumped = repr(response)
        if self.looks_like_html(dumped):
            raise self.host.structured_output_error("gateway returned HTML instead of model output")
        raise self.host.structured_output_error(f"unsupported responses API payload: {type(response).__name__}")

    def extract_json_object(self, text: str) -> dict:
        text = text.strip()
        if not text:
            raise ValueError("empty fallback response")
        if self.looks_like_html(text):
            raise ValueError("gateway returned HTML instead of JSON")
        if text.startswith("```"):
            lines = text.splitlines()
            if len(lines) >= 3 and lines[0].startswith("```") and lines[-1].startswith("```"):
                text = "\n".join(lines[1:-1]).strip()
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end < start:
            raise ValueError("no json object found in response")
        return json.loads(text[start : end + 1])

    def looks_like_html(self, text: str) -> bool:
        lowered = text.lstrip().lower()
        return lowered.startswith(("<!doctype html", "<html"))
