"""
Migrate BPC 4.x form definitions (JSON) to 5.0 format.

Reference: https://docs.virtimo.net/en/bpc-docs/5.0/core/admin/migration/migration_4x_to_5.html#_changes_in_the_forms_module

Usage:
    python migrate_forms.py <input.json> [output.json]

If output is omitted, writes to <input>_migrated.json.
"""

import json
import re
import sys
from copy import deepcopy
from pathlib import Path

# ISO 639-1 codes commonly used in BPC multilingual fields
LANGUAGE_CODES = {
    "de", "en", "fr", "es", "it", "nl", "pt", "pl", "cs", "sk",
    "hu", "ro", "bg", "hr", "sl", "da", "sv", "no", "fi", "el",
    "tr", "ru", "uk", "ar", "zh", "ja", "ko",
}

# Fields known to carry translatable content
TRANSLATABLE_KEYS = {"label", "placeholder", "tooltip", "description", "title", "helpText"}

# Action renames (iframe context)
ACTION_RENAMES = {
    "printForm": "print",
    "resetForm": "reset",
    "setData": "setFormState",
    "submitData": "submit",
    "validateData": "validate",
}

# Binding path pattern: paths like "/data/..." or "/state/..." used in form bindings
BINDING_RE = re.compile(r"^/(data|state)/.")


def looks_like_multilang(obj: dict) -> bool:
    """Return True if every key in the dict is a known language code."""
    return (
        len(obj) > 0
        and all(isinstance(k, str) and k.lower() in LANGUAGE_CODES for k in obj)
        and all(isinstance(v, str) for v in obj.values())
    )


def wrap_multilang(obj: dict) -> dict:
    """Wrap a plain multilingual dict with the MULTI_LANGUAGE tag."""
    if "MULTI_LANGUAGE" in obj:
        return obj  # already wrapped
    if looks_like_multilang(obj):
        return {"MULTI_LANGUAGE": obj}
    return obj


def wrap_binding(value: str) -> str:
    """Wrap a bare binding path like '/data/text' into '${/data/text}'."""
    if isinstance(value, str) and BINDING_RE.match(value) and not value.startswith("${"):
        return f"${{{value}}}"
    return value


def migrate_node(node, parent_key=None):
    """Recursively migrate a single JSON node (dict or list)."""
    if isinstance(node, dict):
        migrated = {}

        # Only rename "request" → "payload" in iframe message objects
        is_iframe_message = "requestName" in node

        for key, value in node.items():

            # --- Remove deprecated keys ---
            if key == "onChangeBufferTime":
                continue

            # --- Rename keys ---
            new_key = key
            if key == "dataUrl":
                new_key = "stateUrl"
            elif key == "requestName":
                new_key = "action"
            elif key == "request" and is_iframe_message:
                if "payload" in node:
                    print(f"WARNING: object has both 'request' and 'payload' keys, "
                          f"skipping rename to avoid data loss", file=sys.stderr)
                else:
                    new_key = "payload"

            # --- Multilingual wrapping ---
            if new_key in TRANSLATABLE_KEYS and isinstance(value, dict):
                migrated[new_key] = wrap_multilang(deepcopy(value))
                # recurse into the (possibly wrapped) value
                migrated[new_key] = migrate_node(migrated[new_key], new_key)
                continue

            # --- Action value renames ---
            if new_key == "action" and isinstance(value, str):
                value = ACTION_RENAMES.get(value, value)

            # --- Binding path wrapping ---
            if new_key in ("value", "instancePath") and isinstance(value, str):
                value = wrap_binding(value)

            migrated[new_key] = migrate_node(value, new_key)

        return migrated

    if isinstance(node, list):
        return [migrate_node(item, parent_key) for item in node]

    # Scalars pass through unchanged
    return node


def migrate_form(form: dict) -> dict:
    """Apply all migration rules to a form definition."""
    return migrate_node(deepcopy(form))


def main():
    if len(sys.argv) < 2:
        print(__doc__.strip())
        sys.exit(1)

    input_path = Path(sys.argv[1])
    if len(sys.argv) >= 3:
        output_path = Path(sys.argv[2])
    else:
        output_path = input_path.with_stem(input_path.stem + "_migrated")

    with open(input_path, encoding="utf-8") as f:
        data = json.load(f)

    # Support both a single form object and an array of forms
    if isinstance(data, list):
        migrated = [migrate_form(form) for form in data]
    else:
        migrated = migrate_form(data)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(migrated, f, indent=2, ensure_ascii=False)

    print(f"Migrated: {input_path} -> {output_path}")


if __name__ == "__main__":
    main()
