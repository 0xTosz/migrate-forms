"""
Validate a BPC 5.0 form JSON against the forms schema.

Usage:
    python validate_form.py <form.json> [schema.json]

If schema path is omitted, looks for schema.json in the same directory as this script.
"""

import json
import sys
from pathlib import Path

from jsonschema import Draft201909Validator
from jsonschema.exceptions import best_match


def load_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def build_validator(schema: dict) -> Draft201909Validator:
    # The schema references https://json-schema.org/draft/2019-09/schema
    # for the dataSchema field. Provide it as a known resource so validation
    # works offline without fetching remote URLs.
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT201909

    meta_resource = Resource(
        contents=Draft201909Validator.META_SCHEMA,
        specification=DRAFT201909,
    )
    schema_resource = Resource(contents=schema, specification=DRAFT201909)

    registry = Registry().with_resources([
        ("https://json-schema.org/draft/2019-09/schema", meta_resource),
        (schema.get("$id", ""), schema_resource),
    ])
    return Draft201909Validator(schema, registry=registry)


def format_error(error, indent=0) -> str:
    path = "/".join(str(p) for p in error.absolute_path) if error.absolute_path else "(root)"
    prefix = "  " * indent
    lines = [f"{prefix}  [{path}] {error.message}"]

    # Unwrap if/then errors to show the actual cause
    if error.context:
        for sub in sorted(error.context, key=lambda e: list(e.absolute_path)):
            # Skip the generic "if" half — only show the "then" failures and leaf errors
            if sub.validator == "if":
                continue
            lines.append(format_error(sub, indent + 1))

    return "\n".join(lines)


def main():
    if len(sys.argv) < 2:
        print(__doc__.strip())
        sys.exit(1)

    form_path = Path(sys.argv[1])
    if len(sys.argv) >= 3:
        schema_path = Path(sys.argv[2])
    else:
        schema_path = Path(__file__).parent / "schema.json"

    if not schema_path.exists():
        print(f"ERROR: Schema not found at {schema_path}", file=sys.stderr)
        sys.exit(1)

    schema = load_json(schema_path)
    form = load_json(form_path)

    validator = build_validator(schema)
    errors = sorted(validator.iter_errors(form), key=lambda e: list(e.absolute_path))

    if not errors:
        print(f"VALID: {form_path}")
        return

    print(f"INVALID: {form_path} ({len(errors)} error(s))\n")
    for error in errors:
        print(format_error(error))

    # Highlight the single most relevant error
    top = best_match(validator.iter_errors(form))
    if top and len(errors) > 1:
        print(f"\nMost relevant error:\n{format_error(top)}")

    sys.exit(1)


if __name__ == "__main__":
    main()
