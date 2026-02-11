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


def collect_leaf_errors(error):
    """Recursively collect only leaf errors (no sub-context), skipping 'if' validators."""
    if error.validator == "if":
        return []
    if not error.context:
        return [error]
    leaves = []
    for sub in error.context:
        leaves.extend(collect_leaf_errors(sub))
    return leaves


def format_path(error) -> str:
    return "/".join(str(p) for p in error.absolute_path) if error.absolute_path else "(root)"


def format_error_tree(error, indent=0) -> str:
    """Format error with full sub-error tree for verbose output."""
    path = format_path(error)
    prefix = "  " * indent
    lines = [f"{prefix}  [{path}] {error.message}"]
    if error.context:
        for sub in sorted(error.context, key=lambda e: list(e.absolute_path)):
            if sub.validator == "if":
                continue
            lines.append(format_error_tree(sub, indent + 1))
    return "\n".join(lines)


def main():
    if len(sys.argv) < 2:
        print(__doc__.strip())
        sys.exit(1)

    form_path = Path(sys.argv[1])
    verbose = "--verbose" in sys.argv or "-v" in sys.argv
    args = [a for a in sys.argv[2:] if not a.startswith("-")]

    if args:
        schema_path = Path(args[0])
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

    # Collect leaf errors — these are the actual root causes, not cascading wrappers
    all_leaves = []
    for error in errors:
        all_leaves.extend(collect_leaf_errors(error))

    # Deduplicate by (path, message)
    seen = set()
    unique_leaves = []
    for leaf in all_leaves:
        key = (format_path(leaf), leaf.message)
        if key not in seen:
            seen.add(key)
            unique_leaves.append(leaf)

    # Sort deepest-first so root causes appear before their cascade effects
    unique_leaves.sort(key=lambda e: list(e.absolute_path), reverse=True)

    # Identify cascade noise: unevaluatedProperties errors on containers whose
    # only "unexpected" props are the ones the schema would allow for that type.
    # These are caused by a child validation failure, not the container itself.
    CONTAINER_PROPS = {"components", "layout", "languageButton"}
    cascade_indices = set()
    for i, leaf in enumerate(unique_leaves):
        if leaf.validator != "unevaluatedProperties":
            continue
        # Check if this component has type=container/fieldset in the instance
        instance = leaf.instance
        if isinstance(instance, dict) and instance.get("type") in ("container", "fieldset"):
            # The "unexpected" props listed in the message
            unexpected = {p for p in CONTAINER_PROPS if f"'{p}'" in leaf.message}
            if unexpected:
                cascade_indices.add(i)

    root_causes = [l for i, l in enumerate(unique_leaves) if i not in cascade_indices]
    cascades = [l for i, l in enumerate(unique_leaves) if i in cascade_indices]

    print(f"INVALID: {form_path} ({len(root_causes)} error(s))\n")
    for leaf in root_causes:
        print(f"  [{format_path(leaf)}] {leaf.message}")

    if cascades:
        print(f"\n  ({len(cascades)} additional cascade error(s) on parent containers — "
              f"fix the above first)")

    if verbose:
        print(f"\n--- Full error tree ({len(errors)} top-level) ---\n")
        for error in errors:
            print(format_error_tree(error))
            print()

    sys.exit(1)


if __name__ == "__main__":
    main()
