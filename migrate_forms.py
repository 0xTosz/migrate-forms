"""
Migrate BPC 4.x form definitions (JSON) to 5.0 format.

Reference: https://docs.virtimo.net/en/bpc-docs/5.0/core/admin/migration/migration_4x_to_5.html#_changes_in_the_forms_module

Usage:
    python migrate_forms.py <input.json> [output.json]
    python migrate_forms.py <input_dir/> [output_dir/]

Single file: if output is omitted, writes to <input>_migrated.json.
Directory:   if output is omitted, writes to <input_dir>_migrated/.
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

# 4.x component types that should map to "container" in 5.0
CONTAINER_TYPE_ALIASES = {
    "panel", "form", "vbox", "hbox", "tabpanel", "window",
    "toolbar", "fieldcontainer",
}

# Component types that support readOnly in 5.0
READONLY_SUPPORTED_TYPES = {
    "container", "combobox", "datefield", "fieldset",
    "numberfield", "textarea", "textfield",
}

# Component types that support layout in 5.0
LAYOUT_SUPPORTED_TYPES = {"container", "fieldset"}


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

        # Infer "type": "container" for components with nested children but no type.
        # Only applies inside a "components" array, not at the top-level form object.
        if parent_key == "components" and "components" in node and "type" not in node:
            migrated["type"] = "container"

        # Rename "items" → "components" (old ExtJS convention)
        if "items" in node and "components" not in node and parent_key == "components":
            node["components"] = node.pop("items")
            print(f"INFO: Renamed 'items' -> 'components'", file=sys.stderr)

        for key, value in node.items():

            # --- Remove deprecated keys ---
            if key == "onChangeBufferTime":
                continue

            if key == "baseColor":
                print(f"WARNING: Removed 'baseColor: {value!r}' from configuration. "
                      f"If needed, move it to configuration.styles.variables as a CSS custom property.",
                      file=sys.stderr)
                continue

            # --- layout on unsupported types -> drop ---
            if key == "layout":
                comp_type = node.get("type", "")
                if comp_type and comp_type not in LAYOUT_SUPPORTED_TYPES:
                    print(f"WARNING: Removed 'layout: {value!r}' from '{comp_type}' "
                          f"(layout only supported on container and fieldset)",
                          file=sys.stderr)
                    continue
                # supported type or unknown type — fall through

            # --- readOnly on unsupported types -> disabled ---
            if key == "readOnly":
                comp_type = node.get("type", "")
                if comp_type and comp_type not in READONLY_SUPPORTED_TYPES:
                    if value:
                        migrated["disabled"] = True
                        print(f"INFO: Converted 'readOnly: true' -> 'disabled: true' "
                              f"on '{comp_type}' (readOnly not supported on this type)",
                              file=sys.stderr)
                    # readOnly: false on unsupported type — just drop it
                    continue
                # supported type — fall through to normal handling

            # --- allowBlank -> required ---
            if key == "allowBlank":
                if value is False:
                    migrated["required"] = True
                    print("INFO: Replaced 'allowBlank: false' -> 'required: true'",
                          file=sys.stderr)
                # allowBlank: true means not required, which is the default — drop it
                continue

            # --- Rename keys ---
            new_key = key
            if key == "dataUrl":
                new_key = "stateUrl"

            # --- Map old 4.x component types to "container" ---
            if key == "type" and parent_key == "components" and isinstance(value, str):
                if value.lower() in CONTAINER_TYPE_ALIASES:
                    print(f"INFO: Renamed component type '{value}' -> 'container'",
                          file=sys.stderr)
                    value = "container"
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


def migrate_file(input_path: Path, output_path: Path):
    """Migrate a single JSON file and write the result."""
    try:
        with open(input_path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        print(f"SKIP: {input_path} ({e})", file=sys.stderr)
        return

    if isinstance(data, list):
        migrated = [migrate_form(form) for form in data]
    else:
        migrated = migrate_form(data)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(migrated, f, indent=2, ensure_ascii=False)

    print(f"Migrated: {input_path} -> {output_path}")


def main():
    if len(sys.argv) < 2:
        print(__doc__.strip())
        sys.exit(1)

    input_path = Path(sys.argv[1])
    output_arg = Path(sys.argv[2]) if len(sys.argv) >= 3 else None

    if input_path.is_dir():
        output_dir = output_arg if output_arg else input_path.parent / (input_path.name + "_migrated")
        files = [f for f in input_path.iterdir() if f.is_file()]
        if not files:
            print(f"No files found in {input_path}", file=sys.stderr)
            sys.exit(1)
        for f in files:
            migrate_file(f, output_dir / f.name)
    else:
        output_path = output_arg if output_arg else input_path.with_stem(input_path.stem + "_migrated")
        migrate_file(input_path, output_path)


if __name__ == "__main__":
    main()
