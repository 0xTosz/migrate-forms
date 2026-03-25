"""
Migrate BPC 4.x process starter configurations (JSON) to 5.0 format.

References:
    https://docs.virtimo.net/en/bpc-docs/5.0/core/admin/migration/migration_4x_to_5.html
    https://docs.virtimo.net/en/bpc-docs/5.0/monitor/admin/process_starter.html

Changes applied:
    - Multilingual fields wrapped with MULTI_LANGUAGE tag
      Before: "label": {"de": "Text", "en": "Text"}
      After:  "label": {"MULTI_LANGUAGE": {"de": "Text", "en": "Text"}}
    - Warnings for unknown parameter types

Warnings emitted per process:
    - bpcFormsId references (need separate form migration)
    - startWithContext (backend must accept new records array)
    - dataEndpoint / parametersEndpoint (backend must accept new JSON format)
    - choice parameters with url / mode:"initialRemote" (remote loading changed)
    - mandant was implicitly sent in 4.x and is removed in 5.0

Usage:
    python migrate_process_starters.py <input.json> [output.json]

If output is omitted, writes to <input>_migrated.json.
"""

import json
import sys
from copy import deepcopy
from pathlib import Path

# ISO 639-1 codes commonly used in BPC multilingual fields
LANGUAGE_CODES = {
    "de", "en", "fr", "es", "it", "nl", "pt", "pl", "cs", "sk",
    "hu", "ro", "bg", "hr", "sl", "da", "sv", "no", "fi", "el",
    "tr", "ru", "uk", "ar", "zh", "ja", "ko",
}

# Fields known to carry translatable content in process starter configs
TRANSLATABLE_KEYS = {
    "label", "description", "tooltip", "regexText",
}

# Valid 5.0 process starter parameter types
VALID_PARAMETER_TYPES = {
    "text", "textarea", "number", "boolean", "choice", "date",
    "dateFromTo", "month", "time", "spinner", "upload", "table",
}


def warn(process_key, msg):
    """Print a warning tied to a specific process key."""
    print(f"  WARNING [{process_key}]: {msg}", file=sys.stderr)


def note(msg):
    """Print a general migration note."""
    print(f"  NOTE: {msg}", file=sys.stderr)


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
        return obj
    if looks_like_multilang(obj):
        return {"MULTI_LANGUAGE": obj}
    return obj


def migrate_node(node, parent_key=None, process_key="?"):
    """Recursively migrate a single JSON node (dict or list)."""
    if isinstance(node, dict):
        migrated = {}

        for key, value in node.items():
            # --- Validate process starter parameter types ---
            if key == "type" and parent_key == "parameters" and isinstance(value, str):
                if value not in VALID_PARAMETER_TYPES:
                    warn(process_key,
                         f"Unknown parameter type '{value}', not in 5.0 schema")

            # --- Multilingual wrapping ---
            if key in TRANSLATABLE_KEYS and isinstance(value, dict):
                migrated[key] = wrap_multilang(deepcopy(value))
                migrated[key] = migrate_node(migrated[key], key,
                                             process_key=process_key)
                continue

            migrated[key] = migrate_node(value, key, process_key=process_key)

        return migrated

    if isinstance(node, list):
        return [migrate_node(item, parent_key, process_key=process_key)
                for item in node]

    return node


def audit_process(proc: dict):
    """Scan a single process definition and emit targeted warnings."""
    key = proc.get("key", "???")

    # --- bpcFormsId: referenced form needs separate migration ---
    forms_id = proc.get("bpcFormsId")
    if forms_id:
        warn(key, f'uses bpcFormsId "{forms_id}" — that form definition '
                   f'needs separate migration (migrate_forms.py)')

    # --- startWithContext: records format changed ---
    if proc.get("startWithContext"):
        warn(key, "has startWithContext=true — backend flow must accept "
                  '"records" array (JSON) instead of XML <data> structure')

    # --- dataEndpoint: backend must accept new JSON request format ---
    endpoint = proc.get("dataEndpoint")
    if endpoint:
        warn(key, f'has dataEndpoint "{endpoint}" — backend at that URL '
                   'must accept the new JSON request format')

    # --- parametersEndpoint: same concern ---
    params_ep = proc.get("parametersEndpoint")
    if params_ep:
        warn(key, f'has parametersEndpoint "{params_ep}" — backend at that '
                   'URL must accept the new JSON request format')

    # --- Scan parameters for remote choice loading ---
    for param in proc.get("parameters", []):
        param_key = param.get("key", "???")

        # Option with url field
        for opt in param.get("options", []):
            url = opt.get("url")
            if url:
                warn(key, f'parameter "{param_key}" has choice option with '
                          f'url "{url}" — remote choice loading now goes '
                          f'through VPS endpoint processor with '
                          f'metadata.operation="choiceList"')
                break  # one warning per parameter is enough

        # mode: initialRemote
        if param.get("mode") == "initialRemote":
            warn(key, f'parameter "{param_key}" uses mode="initialRemote" — '
                       'remote data loading request format changed to JSON')

        # reloadRemoteData
        if param.get("reloadRemoteData"):
            warn(key, f'parameter "{param_key}" uses reloadRemoteData — '
                       'remote data loading request format changed to JSON')

        # table columns — check for remote choice options inside columns
        for col in param.get("columns", []):
            col_key = col.get("key", "???")
            for opt in col.get("options", []):
                if opt.get("url"):
                    warn(key, f'table column "{col_key}" in parameter '
                              f'"{param_key}" has choice option with url — '
                              f'remote choice loading format changed')
                    break


def migrate_starter(config: dict) -> dict:
    """Apply all migration rules to a process starter configuration."""
    return migrate_node(deepcopy(config))


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

    if isinstance(data, list):
        migrated = [migrate_starter(item) for item in data]
    else:
        migrated = migrate_starter(data)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(migrated, f, indent=2, ensure_ascii=False)

    print(f"Migrated: {input_path} -> {output_path}")

    # --- Per-process audit warnings ---
    print("\n--- Per-Process Warnings ---", file=sys.stderr)

    processes = []
    if isinstance(data, dict):
        processes = data.get("processes", [])
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                processes.extend(item.get("processes", []))

    has_warnings = False
    for proc in processes:
        audit_process(proc)
        has_warnings = True  # audit_process prints directly

    if not processes:
        print("  (no processes found in input)", file=sys.stderr)

    # --- mandant note (applies to all processes) ---
    print("\n--- Removed Implicit Fields ---", file=sys.stderr)
    note('"mandant" was sent automatically in every 4.x request and is '
         'removed in 5.0.')
    note('If your backend flows read it, add '
         '"metadata": {"mandant": "<value>"} to each process definition.')
    note('Other removed implicit fields: portletArchiveName, operation, '
         'gridID, bpcModule, bpcModuleInstanceId.')
    note('If your flows read any of these, add them to "metadata" as well.')

    # --- Monitor settings ---
    print("\n--- Monitor Settings ---", file=sys.stderr)
    note("Function_ProcessStarterEndpoint  -> Function_VpsEndpointProcessor")
    note("Function_InubitBackendConnection  -> REMOVED")
    note("Function_InubitBaseURL            -> REMOVED")

    # --- Backend format ---
    print("\n--- Backend Request Format ---", file=sys.stderr)
    note("Request format changed from XML/form-data to JSON.")
    note("New structure: { config, bpcUrl, records, metadata }")
    note("Flows receiving process starter requests must be updated.")


if __name__ == "__main__":
    main()
