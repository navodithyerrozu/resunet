#!/usr/bin/env python3
"""Emit `components["schemas"]` for packages/shared/src/generated/api.ts.

WHY THIS EXISTS ALONGSIDE gen-types.sh
--------------------------------------
`scripts/gen-types.sh` is the authority. It imports `app.main:app`, calls
`.openapi()`, and pipes the result through `openapi-typescript`, so it reflects
exactly what FastAPI serves -- including response-model filtering, examples, and
the `Model-Input`/`Model-Output` split Pydantic v2 introduces.

It also requires the API's Python environment to be installed. This script does
not: it reads `apps/api/app/models/*.py` with `ast` and derives the same schema
set statically. That makes it useful in two situations the shell script cannot
cover:

  1. Bootstrapping, before anyone has run `uv sync` in apps/api.
  2. CI contract checking on a frontend-only runner -- `--check` exits non-zero
     when api.ts and the models disagree, which is much cheaper than standing up
     the whole backend to catch a renamed field.

DELIBERATE DIVERGENCE FROM THE REAL GENERATOR
---------------------------------------------
FastAPI marks a field required iff it has no default, and with Pydantic v2 it
emits separate `-Input` and `-Output` schemas when those shapes differ. Emitting
that faithfully would double the type surface and make every response field
optional at the type level, even though the server always serializes it.

So: response models get every field required, request models (*Create, *Update,
*Request, *Login) mark defaulted fields optional. That matches how the types are
actually consumed. When `gen-types.sh` first runs it will produce the Input/Output
pairs and this file's output will differ -- that is expected, and the shell
script's answer wins.

Usage:
    python3 scripts/gen_types_offline.py            # rewrite the schemas block
    python3 scripts/gen_types_offline.py --check    # exit 1 on any disagreement
"""

from __future__ import annotations

import argparse
import ast
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
MODELS_DIR = ROOT / "apps" / "api" / "app" / "models"
TARGET = ROOT / "packages" / "shared" / "src" / "generated" / "api.ts"

BEGIN = "    /* BEGIN GENERATED SCHEMAS */"
END = "    /* END GENERATED SCHEMAS */"

# Schemas that exist in the OpenAPI document but have no single Pydantic class
# behind them: generic instantiations and the error envelope built by hand in
# app/core/errors.py.
HAND_WRITTEN = ("Page_", "ErrorBody")

SCALARS = {
    "str": "string",
    "EmailStr": "string",
    "PyObjectId": "string",
    "int": "number",
    "float": "number",
    "bool": "boolean",
    "datetime": "string",
    "date": "string",
    "UUID": "string",
    "Any": "unknown",
    "None": "null",
}

REQUEST_SUFFIXES = ("Create", "Update", "Request", "Login", "Ingest")


def to_camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(word[:1].upper() + word[1:] for word in rest)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


class Model:
    def __init__(self, name: str, bases: list[str], file: str) -> None:
        self.name = name
        self.bases = bases
        self.file = file
        self.fields: list[tuple[str, ast.expr, bool]] = []  # (name, annotation, has_default)
        self.computed: list[tuple[str, ast.expr]] = []
        self.enum_values: list[str] = []


def parse_models() -> dict[str, Model]:
    models: dict[str, Model] = {}

    for path in sorted(MODELS_DIR.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        # This script may run on an interpreter older than the project's 3.12,
        # which cannot parse PEP 695 type parameters. Strip them: the generic
        # `Page[T]` is hand-written in the TS file anyway.
        source = re.sub(r"^(\s*(?:class|def)\s+\w+)\[[^\]]*\]", r"\1", source, flags=re.M)
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue

            bases = [b.id for b in node.bases if isinstance(b, ast.Name)]
            model = Model(node.name, bases, path.name)

            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    if field_is_excluded(item.value):
                        continue
                    model.fields.append((item.target.id, item.annotation, item.value is not None))
                elif isinstance(item, ast.Assign):
                    # StrEnum members.
                    for target in item.targets:
                        if isinstance(target, ast.Name) and target.id.isupper():
                            if isinstance(item.value, ast.Constant):
                                model.enum_values.append(str(item.value.value))
                elif isinstance(item, ast.FunctionDef):
                    decorated = any(
                        (isinstance(d, ast.Name) and d.id == "computed_field")
                        or (isinstance(d, ast.Call) and getattr(d.func, "id", "") == "computed_field")
                        for d in item.decorator_list
                    )
                    if decorated and item.returns is not None:
                        model.computed.append((item.name, item.returns))

            models[node.name] = model

    return models


def field_is_excluded(value: ast.expr | None) -> bool:
    """Mirror Pydantic serialization schemas by omitting ``Field(exclude=True)``."""
    if not isinstance(value, ast.Call):
        return False
    function_name = value.func.id if isinstance(value.func, ast.Name) else None
    if function_name != "Field":
        return False
    return any(
        keyword.arg == "exclude"
        and isinstance(keyword.value, ast.Constant)
        and keyword.value.value is True
        for keyword in value.keywords
    )


def ancestry(models: dict[str, Model], name: str, seen: tuple[str, ...] = ()) -> list[str]:
    if name in seen or name not in models:
        return [name]
    out = [name]
    for base in models[name].bases:
        out.extend(ancestry(models, base, seen + (name,)))
    return out


def is_enum(models: dict[str, Model], name: str) -> bool:
    return "StrEnum" in ancestry(models, name) and bool(models[name].enum_values)


def casing(models: dict[str, Model], name: str) -> str | None:
    """camelCase for ApiModel descendants, snake_case for everything else.

    ApiModel sets a camel alias generator; MongoModel and bare AppModel do not.
    That three-tier split is the wire-format wart documented in api.ts -- this
    function is where it becomes concrete.
    """
    line = ancestry(models, name)
    if "ApiModel" in line:
        return "camel"
    if "MongoModel" in line or "AppModel" in line:
        return "snake"
    return None


# ---------------------------------------------------------------------------
# Type rendering
# ---------------------------------------------------------------------------


def render_type(node: ast.expr, models: dict[str, Model]) -> str:
    if isinstance(node, ast.Constant):
        if node.value is None:
            return "null"
        if isinstance(node.value, str):
            # A string annotation, e.g. `"Job"` for a forward reference.
            return render_type(ast.parse(node.value, mode="eval").body, models)
        return "unknown"

    if isinstance(node, ast.Name):
        if node.id in SCALARS:
            return SCALARS[node.id]
        if node.id in models:
            return f'components["schemas"]["{node.id}"]'
        return "unknown"

    if isinstance(node, ast.Attribute):
        return render_type(ast.Name(id=node.attr), models)

    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        left = render_type(node.left, models)
        right = render_type(node.right, models)
        parts = [p for p in (left, right) if p != "null"]
        rendered = " | ".join(dict.fromkeys(parts))
        return f"{rendered} | null" if "null" in (left, right) else rendered

    if isinstance(node, ast.Subscript):
        base = node.value.id if isinstance(node.value, ast.Name) else getattr(node.value, "attr", "")
        args = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]

        if base in {"list", "List", "Sequence", "set", "Set", "frozenset"}:
            inner = render_type(args[0], models)
            return f"({inner})[]" if "|" in inner else f"{inner}[]"
        if base in {"dict", "Dict", "Mapping"}:
            value = render_type(args[1], models) if len(args) > 1 else "unknown"
            return f"Record<string, {value}>"
        if base in {"Optional"}:
            return f"{render_type(args[0], models)} | null"
        if base in {"Literal"}:
            return " | ".join(
                f'"{a.value}"' if isinstance(a.value, str) else str(a.value)
                for a in args
                if isinstance(a, ast.Constant)
            )
        if base in {"Annotated"}:
            return render_type(args[0], models)
        if base in {"Union"}:
            rendered = [render_type(a, models) for a in args]
            parts = [p for p in rendered if p != "null"]
            joined = " | ".join(dict.fromkeys(parts))
            return f"{joined} | null" if "null" in rendered else joined
        if base == "Page":
            inner = render_type(args[0], models)
            return f"Page<{inner}>"

    return "unknown"


def inherited_fields(
    models: dict[str, Model], name: str
) -> list[tuple[str, ast.expr, bool]]:
    """Own fields plus inherited ones, base-first, then computed fields."""
    out: list[tuple[str, ast.expr, bool]] = []
    seen: set[str] = set()

    for ancestor in reversed(ancestry(models, name)):
        model = models.get(ancestor)
        if model is None:
            continue
        for field in model.fields:
            if field[0] not in seen:
                seen.add(field[0])
                out.append(field)

    for ancestor in reversed(ancestry(models, name)):
        model = models.get(ancestor)
        if model is None:
            continue
        for prop_name, annotation in model.computed:
            if prop_name not in seen:
                seen.add(prop_name)
                # Computed fields are always serialized, never optional.
                out.append((prop_name, annotation, False))

    return out


def render_schema(models: dict[str, Model], name: str) -> str:
    model = models[name]

    if is_enum(models, name):
        union = " | ".join(f'"{v}"' for v in model.enum_values)
        if len(union) <= 74:
            return f"    {name}: {union};"
        lines = "\n".join(f'      | "{v}"' for v in model.enum_values)
        return f"    {name}:\n{lines};"

    style = casing(models, name)
    optional = name.endswith(REQUEST_SUFFIXES)

    rows: list[str] = []
    for field_name, annotation, has_default in inherited_fields(models, name):
        # MongoModel aliases `id` to `_id` for storage, but `model_dump()` without
        # by_alias -- which is what FastAPI response serialization uses -- emits
        # `id`. So the wire key is `id`, not `_id`.
        key = to_camel(field_name) if style == "camel" else field_name
        mark = "?" if optional and has_default else ""
        rows.append(f"      {key}{mark}: {render_type(annotation, models)};")

    if not rows:
        return f"    {name}: Record<string, never>;"

    body = "\n".join(rows)
    return f"    {name}: {{\n{body}\n    }};"


def emit(models: dict[str, Model]) -> str:
    exported = sorted(
        n
        for n, m in models.items()
        if n not in {"AppModel", "ApiModel", "MongoModel", "Page"}
        and (casing(models, n) is not None or is_enum(models, n))
        and not n.startswith("_")
    )
    return "\n\n".join(render_schema(models, name) for name in exported)


# ---------------------------------------------------------------------------
# Splice and check
# ---------------------------------------------------------------------------


def splice(existing: str, generated: str) -> str:
    start = existing.index(BEGIN)
    stop = existing.index(END)
    return existing[: start + len(BEGIN)] + "\n\n" + generated + "\n\n" + existing[stop:]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="exit 1 if api.ts is stale")
    args = parser.parse_args()

    models = parse_models()
    generated = emit(models)
    existing = TARGET.read_text(encoding="utf-8")

    if BEGIN not in existing or END not in existing:
        print(f"error: {TARGET} is missing the generated-schema markers", file=sys.stderr)
        return 2

    updated = splice(existing, generated)

    if args.check:
        if updated != existing:
            print(
                "generated/api.ts disagrees with apps/api/app/models.\n"
                "Run: python3 scripts/gen_types_offline.py",
                file=sys.stderr,
            )
            return 1
        print("generated/api.ts matches the Pydantic models")
        return 0

    TARGET.write_text(updated, encoding="utf-8")
    count = len([line for line in generated.split("\n") if re.match(r"^    \w+:", line)])
    print(f"wrote {count} schemas to {TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
