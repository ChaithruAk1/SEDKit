"""Generated AI artefacts: skill output_schema.json files and `// <generated:schemas>` blocks in workflows (ws1-ai).

The Pydantic models are the only source of truth. For every installed skill that has a handler:
* `.claude/skills/<skill>/output_schema.json` holds the JSON schema of `handler.output_model` (when the folder exists);
* every workflow the skill declares gets `const SCHEMAS = {RunPlan, BatchResult, FinishSummary, output}` between the
  `// <generated:schemas>` and `// </generated:schemas>` marker lines.
Schemas are fully inlined (no `$ref`/`$defs`), so a workflow can nest them inside its own agent schemas.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from sed.ai.contract import BatchResult, FinishSummary, RunPlan
from sed.errors import ValidationFailed

START_MARKER = "// <generated:schemas>"
END_MARKER = "// </generated:schemas>"


def _inline(node: Any, defs: dict[str, Any], stack: tuple[str, ...] = ()) -> Any:
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            name = ref.removeprefix("#/$defs/")
            if name in stack:
                raise ValidationFailed(f"Recursive schema '{name}' cannot be inlined")
            merged = {**defs[name], **{k: v for k, v in node.items() if k != "$ref"}}
            return _inline(merged, defs, (*stack, name))
        return {k: _inline(v, defs, stack) for k, v in node.items() if k != "$defs"}
    if isinstance(node, list):
        return [_inline(v, defs, stack) for v in node]
    return node


def model_schema(model: type[BaseModel]) -> dict[str, Any]:
    """JSON schema of a model with every local $ref inlined."""
    schema = model.model_json_schema()
    return _inline(schema, schema.get("$defs", {}))


def _json(data: Any) -> str:
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def schemas_block(output_models: dict[str, type[BaseModel]]) -> str:
    schemas: dict[str, Any] = {
        "RunPlan": model_schema(RunPlan),
        "BatchResult": model_schema(BatchResult),
        "FinishSummary": model_schema(FinishSummary),
    }
    if len(output_models) == 1:
        schemas["output"] = model_schema(next(iter(output_models.values())))
    else:
        schemas["outputs"] = {name: model_schema(m) for name, m in sorted(output_models.items())}
    body = json.dumps(schemas, indent=2, sort_keys=True, ensure_ascii=False)
    return f"const SCHEMAS = {body};\n"


def replace_block(text: str, block: str, *, label: str) -> str:
    lines = text.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if line.strip().startswith(START_MARKER)]
    ends = [i for i, line in enumerate(lines) if line.strip().startswith(END_MARKER)]
    if len(starts) != 1 or len(ends) != 1 or ends[0] < starts[0]:
        raise ValidationFailed(f"{label}: expected exactly one '{START_MARKER}' ... '{END_MARKER}' block")
    return "".join([*lines[: starts[0] + 1], block, *lines[ends[0] :]])


def _differs(path: Path, text: str) -> bool:
    return (path.read_bytes().decode("utf-8") if path.is_file() else None) != text


def generated_files(repo: Path) -> dict[Path, str]:
    """Expected content of every generated AI file (path -> text)."""
    from sed.modules import handler as load_handler
    from sed.modules import installed

    out: dict[Path, str] = {}
    workflows: dict[str, dict[str, type[BaseModel]]] = {}
    for module in installed(include_extra=False):
        for sdef in module.skills:
            if not sdef.handler:
                continue
            model = load_handler(sdef.name).output_model
            skill_dir = repo / ".claude" / "skills" / sdef.name
            if skill_dir.is_dir():
                out[skill_dir / "output_schema.json"] = _json(model_schema(model))
            for wf in sdef.workflows:
                workflows.setdefault(wf, {})[sdef.name] = model
    for wf, models in sorted(workflows.items()):
        path = repo / ".claude" / "workflows" / wf
        if not path.is_file():
            raise ValidationFailed(f"Workflow {path.relative_to(repo).as_posix()} declared by a skill is missing")
        current = path.read_bytes().decode("utf-8")
        out[path] = replace_block(current, schemas_block(models), label=path.relative_to(repo).as_posix())
    return out


def export(repo: Path, *, check: bool) -> tuple[list[str], list[str]]:
    """(drifted, written) repo-relative paths. With check=True nothing is written."""
    try:
        files = generated_files(repo)
    except ValidationFailed as exc:
        if check:
            return [exc.message], []
        raise
    drifted = [path for path, text in files.items() if _differs(path, text)]
    if not check:
        for path in drifted:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(files[path].encode("utf-8"))
    rels = [p.relative_to(repo).as_posix() for p in drifted]
    return (rels, []) if check else ([], rels)


def export_all(repo: Path, *, check: bool) -> list[str]:
    """Write (or with check=True, compare) generated files. Returns repo-relative paths that drifted."""
    return export(repo, check=check)[0]
