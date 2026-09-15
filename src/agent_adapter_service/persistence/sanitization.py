"""Store a bounded structural summary, never arbitrary argument strings.

Only known operational scalar keys retain values. Unknown values (including free
text and unknown credential names) are redacted. Actor must be an opaque ID;
caller-controlled text does not belong in the audit metadata fields.
"""

from pydantic import JsonValue

REDACTED = "[REDACTED]"
SAFE_NUMBERS = {"quantity", "count", "limit", "offset", "item_count", "page", "page_size"}
SAFE_ENUMS = {
    "currency": {"USD", "EUR", "GBP", "CNY", "JPY"},
    "risk_level": {"low", "medium", "high"},
    "policy": {"auto", "confirm", "deny"},
}
SENSITIVE_KEYS = {
    "password",
    "token",
    "secret",
    "authorization",
    "cookie",
    "payment",
    "card",
    "email",
    "phone",
    "address",
    "name",
    "cvv",
    "cvc",
    "credential",
    "apikey",
}
FIELD_NAMES = (
    SAFE_NUMBERS
    | SAFE_ENUMS.keys()
    | SENSITIVE_KEYS
    | {
        "items",
        "arguments",
        "headers",
        "product_id",
        "variant_id",
        "order_id",
        "customer_id",
        "saleor_user_id",
        "visitor_id",
        "channel",
        "locale",
        "free_text",
        "query",
        "nested",
        "card_number",
        "access_token",
        "refresh_token",
        "api_key",
        "_truncated",
    }
)


def sanitize_arguments(arguments: dict[str, JsonValue]) -> dict[str, JsonValue]:
    def clean(value: JsonValue, key: str = "", depth: int = 0) -> JsonValue:
        if any(word in key.replace("_", "") for word in SENSITIVE_KEYS):
            return REDACTED
        if depth >= 5:
            return REDACTED
        if isinstance(value, dict):
            result: dict[str, JsonValue] = {}
            for index, (child_key, child_value) in enumerate(value.items()):
                if index >= 50:
                    result["_truncated"] = True
                    break
                # Unknown field names may themselves be credentials or personal data.
                safe_key = child_key if child_key.lower() in FIELD_NAMES else f"field_{index}"
                result[safe_key] = clean(child_value, child_key.lower(), depth + 1)
            return result
        if isinstance(value, list):
            return [clean(item, key, depth + 1) for item in value[:20]]
        if key == "_truncated" and value is True:
            return True
        if key in SAFE_NUMBERS and type(value) in (int, float) and abs(value) <= 1_000_000:
            return value
        if key in SAFE_ENUMS and isinstance(value, str) and value in SAFE_ENUMS[key]:
            return value
        return REDACTED

    result = clean(arguments)
    assert isinstance(result, dict)
    return result
