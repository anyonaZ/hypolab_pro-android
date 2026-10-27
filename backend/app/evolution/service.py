from __future__ import annotations

import json
import math
import random
import re
import time

from sqlalchemy import delete, desc, select

from app.brain import engine
from app.db import models as M
from app.db.base import SessionLocal
from app.generation import service as gen
from app.simulation import service as sim
import validator as V


WINDOWS = [2, 5, 10, 20, 40, 60, 90, 120, 180, 252]
GROUPS = ["subindustry", "industry", "sector", "market"]
OP_ALTERNATIVES = {
    "rank": ["zscore", "scale"], "zscore": ["rank", "scale"], "scale": ["rank", "zscore"],
    "ts_rank": ["ts_zscore", "ts_scale", "ts_delta"],
    "ts_zscore": ["ts_rank", "ts_scale", "ts_delta"],
    "ts_scale": ["ts_rank", "ts_zscore", "ts_delta"],
    "ts_mean": ["ts_median", "ts_sum"], "ts_median": ["ts_mean"], "ts_sum": ["ts_mean"],
    "ts_std_dev": ["ts_av_diff"], "ts_av_diff": ["ts_std_dev"],
    "group_rank": ["group_zscore", "group_scale"],
    "group_zscore": ["group_rank", "group_scale"], "group_scale": ["group_rank", "group_zscore"],
    "vec_avg": ["vec_sum", "vec_norm", "vec_max", "vec_min"],
    "vec_sum": ["vec_avg", "vec_norm"], "vec_max": ["vec_avg", "vec_min"], "vec_min": ["vec_avg", "vec_max"],
}


def _serialize(node) -> str:
    """Stable FastExpr serialization used for reproducible identity and hashing."""
    if isinstance(node, V.Num): return node.raw
    if isinstance(node, V.Str): return node.raw
    if isinstance(node, V.Ident): return node.name
    if isinstance(node, V.KwArg): return f"{node.name}={_serialize(node.value)}"
    if isinstance(node, V.Call): return f"{node.name}({', '.join(_serialize(x) for x in node.args)})"
    if isinstance(node, V.Unary): return f"{node.op}({_serialize(node.operand)})"
    if isinstance(node, V.BinOp): return f"({_serialize(node.left)} {node.op} {_serialize(node.right)})"
    if isinstance(node, V.Seq): return "; ".join(_serialize(x) for x in node.statements)
    raise TypeError(type(node).__name__)


def canonical(expression: str) -> str:
    return _serialize(V.parse(expression.strip()))


def expression_hash(expression: str) -> str:
    import hashlib
    return hashlib.sha256(canonical(expression).encode("utf-8")).hexdigest()


PRESETS = {
    "quick": {"population_size": 8, "generations": 2, "elite_size": 3, "max_operators": 4,
              "mutation_rate": .65, "robustness": "pilot"},
    "balanced": {"population_size": 12, "generations": 4, "elite_size": 4, "max_operators": 6,
                 "mutation_rate": .80, "robustness": "promote"},
    "robust": {"population_size": 20, "generations": 6, "elite_size": 6, "max_operators": 8,
               "mutation_rate": .90, "robustness": "full"},
}
MUTATION_GRAMMAR_VERSION = "manual-ast-1"
MUTATION_METHODS = {
    "horizon": "Change lookback windows while preserving the operator structure.",
    "field": "Replace a field inside the chosen dataset/category boundary.",
    "operator": "Swap an operator for a compatible operator from the same family.",
    "atlas": "Introduce compatible operators from the user's complete Operator Atlas.",
    "parameter": "Vary supported keyword parameters declared by Operator Atlas signatures.",
    "sign": "Test whether the economic direction is reversed.",
    "group": "Change the grouping level used by a group operation.",
    "normalize": "Add rank, z-score, or scale normalization.",
    "smooth": "Add a mean, median, or decay horizon around the signal.",
    "difference": "Test changes rather than levels with delta or average-difference transforms.",
    "simplify": "Remove a removable outer transform to test a simpler descendant.",
    "crossover": "Combine two validated parents and revalidate the resulting type flow.",
}

PARAMETER_VALUES = {
    "constant": [0, 0.5, 1], "rate": [1, 2, 3], "std": [2, 3, 4, 6],
    "tolerance": [0.001, 0.01, 0.05], "scale": [0.5, 1, 2], "hump": [0.001, 0.01, 0.05],
    "maxpercent": [0.005, 0.01, 0.02, 0.05], "lag": [0, 1, 2], "rettype": [0, 1, 2],
    "mode": [0, 1, 2], "dense": ["false", "true"], "driver": ['"gaussian"', '"uniform"', '"cauchy"'],
    "factor": [0.25, 0.5, 0.75], "sensitivity": [0.25, 0.5, 0.75], "force": [0.05, 0.1, 0.2],
    "k": [1, 2, 5], "period": [2, 5, 10, 20], "target_tvr": [0.05, 0.1, 0.2],
    "lambda_min": [0, 0.01, 0.05], "lambda_max": [0.5, 0.75, 1],
    "lower": [-1, 0, 0.01], "upper": [0, 0.01, 1], "minimum": [-1, 0, 0.01],
    "maximum": [0, 0.01, 1], "newval": [-1, 0, 1], "value": [-1, 0, 1],
}


def _operator_family(name: str) -> str:
    if name.startswith("vec_"): return "vector"
    if name.startswith("group_"): return "group"
    if "tvr" in name or name in {"hump", "keep", "jump_decay"}: return "turnover-control"
    if name.startswith("ts_"): return "time-series"
    if "regression" in name or name.endswith("_proj") or name.endswith("_neut"): return "regression"
    if name in {"add", "subtract", "multiply", "divide", "power", "signed_power", "max", "min"}: return "arithmetic"
    if name in {"and", "or", "not", "equal", "not_equal", "greater", "greater_equal", "less", "less_equal", "if_else", "trade_when"}: return "logical"
    if name in {"rank", "zscore", "scale", "normalize", "quantile", "winsorize", "truncate", "clamp", "tail", "left_tail", "right_tail"}: return "cross-sectional"
    return "transform"


def operator_atlas_catalog() -> list[dict]:
    """Freeze the user's REGULAR Operator Atlas into a compact, mutation-safe run snapshot."""
    try:
        from app.operators import service as operators
        rows = operators.list_ops("REGULAR")
    except Exception:
        return []
    out = []
    for row in rows:
        params = [{"name": str(p.get("name") or "").strip(), "kind": p.get("kind", "positional"),
                   "required": bool(p.get("required", False)), "default": str(p.get("default") or "")}
                  for p in row.get("params", []) if str(p.get("name") or "").strip()]
        out.append({"name": row["name"], "family": _operator_family(row["name"]),
                    "signature": row.get("signature", ""), "params": params,
                    "user_edited": bool(row.get("user_edited"))})
    return out


def _operator_shape(op: dict) -> tuple:
    positional = [p for p in op.get("params", []) if p.get("kind") == "positional" and p.get("required", True)]
    required_keywords = sorted(p.get("name", "").lower() for p in op.get("params", [])
                               if p.get("kind") == "keyword" and p.get("required"))
    return len(positional), tuple(required_keywords)


def _vector_reducers(atlas: list[dict]) -> list[str]:
    """Vector operators that safely reduce one VECTOR input to a scalar series."""
    return [op["name"] for op in atlas if op.get("family") == "vector" and _operator_shape(op) == (1, ())]


def _atlas_variants(expressions: list[str], atlas: list[dict], windows: list[int], limit: int) -> list[dict]:
    """Build broad Atlas-driven wrappers using signature shape; validation remains authoritative."""
    out: list[dict] = []; seen: set[str] = set()
    cap = max(1, int(limit * .65))  # reserve the remainder for valid multi-input operators
    def add(expr, parent, mutation):
        if expr not in seen and len(out) < cap:
            seen.add(expr); out.append({"expression": expr, "parent": parent, "mutation": mutation})
    unsafe = {"bucket", "densify", "ts_step", "group_cartesian_product", "group_multi_regression", "multi_regression"}
    def values_for(param):
        values = list(PARAMETER_VALUES.get(param["name"].lower(), []))
        default = str(param.get("default") or "").strip()
        if default and default not in {str(x) for x in values}: values.insert(0, default)
        return values
    def add_parameterized(name, args, params, base, mutation):
        keywords = [p for p in params if p["kind"] == "keyword"]
        required = [p for p in keywords if p.get("required")]
        required_parts = []
        for param in required:
            values = values_for(param)
            if not values: return  # never emit a call missing a required keyword
            required_parts.append(f"{param['name']}={values[0]}")
        call_args = [*args, *required_parts]
        add(f"{name}({', '.join(call_args)})", base, mutation)
        for param in keywords:
            for value in values_for(param)[:3]:
                variants = [x for x in required_parts if not x.startswith(param["name"] + "=")]
                variants.append(f"{param['name']}={value}")
                add(f"{name}({', '.join([*args, *variants])})", base,
                    mutation + f" · {param['name']}={value}")
    family_order = ["time-series", "cross-sectional", "group", "regression", "arithmetic",
                    "logical", "turnover-control", "transform", "vector"]
    family_buckets = {family: [op for op in atlas if op["family"] == family] for family in family_order}
    ordered_atlas = []
    while any(family_buckets.values()):
        for family in family_order:
            if family_buckets[family]: ordered_atlas.append(family_buckets[family].pop(0))
    for base in expressions:
        for op in ordered_atlas:
            name, family, params = op["name"], op["family"], op["params"]
            if name in unsafe or name in gen._operators_in(base): continue
            positional = [p for p in params if p["kind"] == "positional"]
            required = [p for p in positional if p.get("required", True)]
            if family == "vector": continue  # introduced only when scalarizing a VECTOR field
            if family == "time-series" and len(required) == 2 and required[1]["name"].lower() in {"d", "days", "window", "lookback", "period", "periods", "l", "length", "lag"}:
                for window in windows: add_parameterized(name, [base, str(window)], params, base, f"atlas {family}: {name} · d={window}")
            elif family == "group" and len(required) == 2 and required[1]["name"].lower() in {"group", "g", "by"}:
                for group in GROUPS: add_parameterized(name, [base, group], params, base, f"atlas {family}: {name} · group={group}")
            elif len(required) == 1 and required[0]["name"].lower() not in {"group", "g"}:
                add_parameterized(name, [base], params, base, f"atlas {family}: {name}")
    cap = limit
    pairs = [(left, right) for i, left in enumerate(expressions[:20]) for right in expressions[i + 1:20]]
    for op in ordered_atlas:
        name, family = op["name"], op["family"]
        required = [p for p in op["params"] if p["kind"] == "positional" and p.get("required", True)]
        for left, right in pairs:
            if family in {"arithmetic", "logical", "regression"} and len(required) == 2:
                add_parameterized(name, [left, right], op["params"], left, f"atlas {family}: {name} · paired input")
            elif family == "time-series" and len(required) == 3 and required[-1]["name"].lower() in {"d", "days", "window", "lookback", "period", "periods", "l", "length"}:
                for window in windows: add_parameterized(name, [left, right, str(window)], op["params"], left, f"atlas {family}: {name} · paired · d={window}")
            elif family == "group" and len(required) == 3 and required[-1]["name"].lower() in {"group", "g", "by"}:
                for group in GROUPS: add_parameterized(name, [left, right, group], op["params"], left, f"atlas {family}: {name} · paired · group={group}")
            if len(out) >= limit: return out
    return out


def _parameter_variants(expressions: list[str], atlas: list[dict], limit: int) -> list[dict]:
    out: list[dict] = []; seen: set[str] = set()
    declared = {o["name"]: {p["name"].lower() for p in o["params"]} for o in atlas}
    for base in expressions:
        for op in gen._operators_in(base):
            for param in declared.get(op, set()):
                values = PARAMETER_VALUES.get(param)
                if not values: continue
                pattern = re.compile(r"(?i)(?<![\w])" + re.escape(param) + r"\s*=\s*([^,\)]+)")
                match = pattern.search(base)
                if not match: continue
                for value in values:
                    child = base[:match.start(1)] + str(value) + base[match.end(1):]
                    if child != base and child not in seen:
                        seen.add(child); out.append({"expression": child, "parent": base,
                            "mutation": f"parameter {op}.{param}→{value}"})
                    if len(out) >= limit: return out
    return out


def preview(protocol: dict) -> dict:
    preset_name = str(protocol.get("preset", "balanced")).lower()
    preset = dict(PRESETS.get(preset_name, PRESETS["balanced"]))
    fields = protocol.get("fields") or []
    seeds = list(dict.fromkeys(str(x).strip() for x in protocol.get("expressions", []) if str(x).strip()))
    checked = gen.validate_expressions(seeds, fields, preset["max_operators"], multi_field=True)
    max_fields = int((protocol.get("direction") or {}).get("max_fields_per_expression", 2))
    field_ids = {f.get("id") for f in fields}
    for verdict in checked["results"]:
        used = set(gen._fields_in(verdict["expr"], field_ids))
        if verdict["ok"] and (not used or len(used) > max_fields):
            verdict["ok"] = False
            verdict["issues"].append(f"field count {len(used)} exceeds mode limit {max_fields}")
    valid = [canonical(x["expr"]) for x in checked["results"] if x["ok"]]
    invalid = [{"expression": x["expr"], "issues": x["issues"]} for x in checked["results"] if not x["ok"]]
    candidate_count = max(4, min(int(protocol.get("candidate_count", 30)), 400))
    universes = protocol.get("universes") or [protocol.get("universe", "TOP3000")]
    neutralizations = protocol.get("neutralizations") or ["INDUSTRY"]
    configs = max(1, len(universes) * len(neutralizations))
    promoted = 0
    if preset["robustness"] == "promote" and configs > 1:
        promoted = max(preset["elite_size"], math.ceil(candidate_count / 3))
    elif preset["robustness"] == "full" and configs > 1:
        promoted = max(preset["elite_size"], math.ceil(candidate_count / 2))
    promoted = min(candidate_count, promoted)
    robustness_configs = min(max(0, configs - 1), int(protocol.get("robustness_config_limit", configs - 1)))
    max_configurations = candidate_count + promoted * robustness_configs
    field_map = {f.get("id"): f for f in fields if f.get("id")}
    seed_profiles = []
    for expression in valid:
        used = [field_map[x] for x in gen._fields_in(expression, set(field_map))]
        datasets = sorted({str(x.get("dataset_id") or "unknown") for x in used})
        cats = sorted({str(x.get("category") or "unknown") for x in used})
        seed_profiles.append({"expression": expression, "fields": [x["id"] for x in used],
            "datasets": datasets, "categories": cats,
            "relationship": "single field" if len(used) == 1 else
                ("same dataset" if len(datasets) == 1 else "different datasets, same category" if len(cats) == 1
                 else "different datasets and categories")})
    relationship_counts: dict[str, int] = {}
    for profile in seed_profiles:
        relationship_counts[profile["relationship"]] = relationship_counts.get(profile["relationship"], 0) + 1
    generated: list[dict] = []
    generated_ids: set[str] = set()
    parents = valid
    rng = random.Random(int(protocol.get("random_seed", 1)))
    if valid:
        planned = min(50, max(preset["generations"], math.ceil(candidate_count / preset["population_size"])))
        for generation in range(planned):
            wanted = min(preset["population_size"], candidate_count - len(generated))
            if wanted <= 0: break
            batch = _build_unique_population(parents, fields, wanted, generation * preset["population_size"],
                generated_ids, preset["max_operators"], protocol.get("categories") or {},
                protocol.get("direction") or {}, rng)
            for row in batch:
                row = dict(row); row["generation"] = generation + 1; row["status"] = "generated"
                row["expression_hash"] = expression_hash(row["expression"])
                row["complexity"] = _complexity(row["expression"])
                generated.append(row); generated_ids.add(row["expression"])
            if not batch: break
            parents = [x["expression"] for x in batch]
    atlas = protocol.get("direction", {}).get("operator_atlas") or []
    family_by_name = {x["name"]: x["family"] for x in atlas}
    operators_used = sorted({op for row in generated for op in gen._operators_in(row["expression"])})
    families_used = sorted({family_by_name[op] for op in operators_used if op in family_by_name})
    generated_field_counts = {fid: 0 for fid in field_map}
    mutation_counts: dict[str, int] = {}
    for row in generated:
        for fid in set(gen._fields_in(row["expression"], set(field_map))):
            generated_field_counts[fid] = generated_field_counts.get(fid, 0) + 1
        family = str(row.get("mutation", "seed")).split(" ", 1)[0]
        mutation_counts[family] = mutation_counts.get(family, 0) + 1
    rejected = list((protocol.get("direction") or {}).get("rejection_ledger") or [])
    rejection_counts: dict[str, int] = {}
    for row in rejected: rejection_counts[row.get("reason", "validation")] = rejection_counts.get(row.get("reason", "validation"), 0) + 1
    for ordinal, row in enumerate(generated):
        row["settings"] = _genetic_settings(row, protocol, {}, ordinal)
    return {"preset": preset_name, "expanded": preset, "valid_seeds": valid, "invalid_seeds": invalid,
            "seed_profiles": seed_profiles[:8], "relationship_counts": relationship_counts,
            "generated_candidates": generated, "generated_count": len(generated),
            "operator_atlas_count": len(atlas), "operators_used": operators_used,
            "operator_families_used": families_used,
            "field_coverage": generated_field_counts, "mutation_coverage": mutation_counts,
            "rejected_candidates": rejected[:250], "rejection_counts": rejection_counts,
            "field_count": len(fields), "candidate_count": candidate_count,
            "pilot_configurations": candidate_count, "promoted_candidates": promoted,
            "max_configurations": max_configurations,
            "configurations_per_promoted_candidate": 1 + robustness_configs,
            "scope": protocol.get("scope", "selected"),
            "diversity": ["field exposure", "operator family", "horizon", "structure"]}


def create_run(protocol: dict) -> int:
    with SessionLocal() as db:
        row = M.EvolutionRun(hypothesis=protocol.get("hypothesis", ""),
                             protocol_json=json.dumps(protocol), status="queued")
        db.add(row); db.commit(); db.refresh(row); return row.id


def _tuple_tree(value):
    return tuple(_tuple_tree(x) for x in value) if isinstance(value, list) else value


def _checkpoint(run_id: int, payload: dict) -> None:
    with SessionLocal() as db:
        row = db.get(M.EvolutionRun, run_id)
        if row:
            row.result_json = json.dumps({"checkpoint": payload})
            row.updated_at = time.time(); db.commit()


def _replace_word(expr: str, old: str, new: str) -> str:
    return re.sub(r"(?<![\w.])" + re.escape(old) + r"(?![\w])", new, expr, count=1)


def _typed_subtrees(expression: str, fields: list[dict]) -> list[tuple[str, str]]:
    """Collect replaceable expression subtrees with a coarse semantic datatype."""
    types={str(f.get("id")):str(f.get("type") or "MATRIX").upper() for f in fields}
    groups=set(GROUPS); out=[]
    try: stack=[V.parse(expression)]
    except Exception: return out
    while stack:
        node=stack.pop(); text=_serialize(node)
        if isinstance(node,V.Ident): kind="group" if node.name in groups else "vector" if types.get(node.name)=="VECTOR" else "scalar"
        elif isinstance(node,V.Call): kind="group" if node.name in {"bucket","densify"} else "scalar"
        else: kind="scalar"
        if text and len(text)<len(expression): out.append((text,kind))
        if isinstance(node,V.Call): stack.extend(node.args)
        elif isinstance(node,V.KwArg): stack.append(node.value)
        elif isinstance(node,V.Unary): stack.append(node.operand)
        elif isinstance(node,V.BinOp): stack.extend([node.left,node.right])
    return out


def _typed_crossover(left: str, right: str, fields: list[dict], rng: random.Random) -> tuple[str,str]:
    """Exchange a datatype-compatible subtree, preserving the receiving parent's structure."""
    a=_typed_subtrees(left,fields); b=_typed_subtrees(right,fields)
    pairs=[(x,y,k) for x,k in a for y,j in b if k==j and x!=y]
    if not pairs: return left,"typed crossover unavailable"
    old,new,kind=rng.choice(pairs)
    return left.replace(old,new,1),f"typed crossover ({kind}) {old}→{new}"


def _mutate(expr: str, fields: list, rng: random.Random, mate: str = "", direction: dict | None = None) -> tuple[str, str]:
    direction = direction or {}
    requested = direction.get("allowed_mutations", list(MUTATION_METHODS))
    choices = [x for x in requested if x in MUTATION_METHODS and x != "crossover"]
    if mate and mate != expr and "crossover" in requested: choices.append("crossover")
    if not choices: return expr, "no permitted mutation available"
    kind = rng.choice(choices)
    out = expr
    if kind == "horizon":
        nums = list(re.finditer(r"(?<![\w.])(?:2|3|4|5|10|20|40|60|90|120|180|252)(?![\w.])", expr))
        if nums:
            m = rng.choice(nums)
            allowed_windows = [int(x) for x in direction.get("windows", WINDOWS) if 1 <= int(x) <= 1000]
            pool = [x for x in (allowed_windows or WINDOWS) if str(x) != m.group()]
            if not pool: return expr, "window unchanged"
            new = str(rng.choice(pool))
            out = expr[:m.start()] + new + expr[m.end():]
            return out, f"horizon {m.group()}→{new}"
    elif kind == "field":
        types = {f.get("id"): str(f.get("type", "MATRIX")).upper() for f in fields}
        used = gen._fields_in(expr, set(types))
        if used:
            forbidden = set(direction.get("forbidden_fields", []))
            old = rng.choice(used); pool = [f for f, t in types.items() if t == types.get(old) and f != old and f not in forbidden]
            if pool:
                new = rng.choice(pool); return _replace_word(expr, old, new), f"field {old}→{new}"
    elif kind == "operator":
        atlas = direction.get("operator_atlas") or []
        family_by_name = {x["name"]: x["family"] for x in atlas}
        atlas_by_name = {x["name"]: x for x in atlas}
        used = [o for o in gen._operators_in(expr) if o in OP_ALTERNATIVES or o in family_by_name]
        if used:
            old = rng.choice(used)
            allowed_ops = set(direction.get("operators", []))
            pool = list(OP_ALTERNATIVES.get(old, []))
            if old in family_by_name:
                old_shape = _operator_shape(atlas_by_name[old])
                pool += [x["name"] for x in atlas if x["family"] == family_by_name[old]
                         and x["name"] != old and _operator_shape(x) == old_shape]
            pool = list(dict.fromkeys(x for x in pool if not allowed_ops or x in allowed_ops))
            if not pool: return expr, "operator unchanged"
            new = rng.choice(pool)
            return re.sub(r"(?<![\w.])" + re.escape(old) + r"(?=\s*\()", new, expr, count=1), f"operator {old}→{new}"
    elif kind == "sign":
        return f"-({expr})" if not expr.startswith("-(") else expr[2:-1], "invert expected sign"
    elif kind == "group":
        present = [g for g in GROUPS if re.search(r"(?<![\w.])" + g + r"(?![\w])", expr)]
        if present:
            old = rng.choice(present); new = rng.choice([g for g in GROUPS if g != old])
            return _replace_word(expr, old, new), f"group {old}→{new}"
    elif kind == "normalize":
        op = rng.choice(["rank", "zscore", "scale"])
        return f"{op}({expr})", f"normalize with {op}"
    elif kind == "smooth":
        op = rng.choice(["ts_mean", "ts_median"]); window = rng.choice(WINDOWS)
        return f"{op}({expr}, {window})", f"smooth with {op} over {window}"
    elif kind == "difference":
        op = rng.choice(["ts_delta", "ts_av_diff"]); window = rng.choice(WINDOWS)
        return f"{op}({expr}, {window})", f"difference with {op} over {window}"
    elif kind == "atlas":
        variants = _atlas_variants([expr], direction.get("operator_atlas") or [],
            direction.get("windows") or WINDOWS, 200)
        if variants:
            row = rng.choice(variants); return row["expression"], row["mutation"]
    elif kind == "parameter":
        variants = _parameter_variants([expr], direction.get("operator_atlas") or [], 100)
        if variants:
            row = rng.choice(variants); return row["expression"], row["mutation"]
    elif kind == "simplify":
        try:
            root = V.parse(expr)
            if isinstance(root, V.Call):
                positional = [x for x in root.args if not isinstance(x, V.KwArg)]
                if positional: return _serialize(positional[0]), f"simplify by removing {root.name}"
        except Exception: pass
    elif kind == "crossover":
        return _typed_crossover(expr,mate,fields,rng)
    if direction:
        return expr, "no permitted mutation available"
    return f"rank({expr})", "cross-sectional normalization"


def _field_variants(expressions: list[str], fields: list[dict], offset: int = 0,
                    vector_ops: list[str] | None = None) -> list[dict]:
    """Create deterministic field substitutions before random mutations.

    A single-field seed otherwise leaves field exploration to chance. Matrix inputs may be
    replaced by a VECTOR after scalarizing it; VECTOR-to-MATRIX substitution is avoided because
    the surrounding vec_* operator would no longer type-check.
    """
    ids = {str(f.get("id")): str(f.get("type") or "MATRIX").upper() for f in fields if f.get("id")}
    targets = list(ids)
    if targets: targets = targets[offset % len(targets):] + targets[:offset % len(targets)]
    out: list[dict] = []
    reducers = vector_ops or ["vec_avg"]
    for expr in expressions:
        used = gen._fields_in(expr, set(ids))
        for old in used:
            for new in targets:
                if new == old: continue
                old_type, new_type = ids.get(old, "MATRIX"), ids.get(new, "MATRIX")
                if old_type == "VECTOR" and new_type != "VECTOR": continue
                replacements = [new] if old_type == new_type else [f"{op}({new})" for op in reducers]
                for replacement in replacements:
                    child = _replace_word(expr, old, replacement)
                    out.append({"expression": child, "parent": expr,
                                "mutation": f"field {old}→{replacement} (selected pool)"})
    return out


def _coverage_population(expressions: list[str], fields: list[dict], size: int, offset: int = 0,
                         vector_ops: list[str] | None = None) -> list[dict]:
    """Build a population that covers selected fields before adding redundant seed shapes."""
    ids = {str(f.get("id")): str(f.get("type") or "MATRIX").upper() for f in fields if f.get("id")}
    targets = list(ids)
    if targets: targets = targets[offset % len(targets):] + targets[:offset % len(targets)]
    population: list[dict] = []
    seen: set[str] = set()
    for index, target in enumerate(targets):
        for seed in expressions[index % len(expressions):] + expressions[:index % len(expressions)]:
            used = gen._fields_in(seed, set(ids))
            if not used: continue
            if target in used:
                child, mutation = seed, "seed selected for field coverage"
            else:
                old = next((x for x in used if ids.get(x) != "VECTOR" or ids[target] == "VECTOR"), None)
                if not old: continue
                reducers = vector_ops or ["vec_avg"]
                replacement = target if ids.get(old) == ids[target] else f"{reducers[index % len(reducers)]}({target})"
                child, mutation = _replace_word(seed, old, replacement), f"field {old}→{replacement} (selected pool)"
            if child not in seen:
                population.append({"expression": child, "parent": "" if child == seed else seed, "mutation": mutation})
                seen.add(child)
                break
        if len(population) >= size: return population
    for seed in expressions:
        if seed not in seen:
            population.append({"expression": seed, "parent": "", "mutation": "seed"})
            seen.add(seed)
        if len(population) >= size: return population
    for variant in _field_variants(expressions, fields, offset, vector_ops):
        if variant["expression"] not in seen:
            population.append(variant); seen.add(variant["expression"])
        if len(population) >= size: break
    return population


def _systematic_variants(expressions: list[str], fields: list[dict], limit: int = 1000,
                         vector_ops: list[str] | None = None, windows: list[int] | None = None) -> list[dict]:
    """Enumerate useful one-step mutations before relying on random sampling.

    This keeps a one-seed study from collapsing when random mutation repeatedly selects an
    unavailable operation.  The caller validates every returned expression, so this function can
    generate broadly while remaining safe for BRAIN.
    """
    out: list[dict] = []; seen = set(expressions)
    def add(expr: str, parent: str, mutation: str):
        if expr and expr not in seen and len(out) < limit:
            seen.add(expr); out.append({"expression": expr, "parent": parent, "mutation": mutation})
    bases = list(expressions)
    horizon_values = windows or WINDOWS
    field_rows = _field_variants(expressions, fields, vector_ops=vector_ops)
    for row in field_rows:
        add(row["expression"], row["parent"], row["mutation"])
    bases += [x["expression"] for x in field_rows]
    for base in bases:
        for match in list(re.finditer(r"(?<![\w.])(?:2|3|4|5|10|20|40|60|90|120|180|252)(?![\w.])", base)):
            for window in horizon_values:
                if str(window) != match.group():
                    add(base[:match.start()] + str(window) + base[match.end():], base,
                        f"horizon {match.group()}→{window}")
        for old in gen._operators_in(base):
            for new in OP_ALTERNATIVES.get(old, []):
                add(re.sub(r"(?<![\w.])" + re.escape(old) + r"(?=\s*\()", new, base, count=1),
                    base, f"operator {old}→{new}")
        for old in GROUPS:
            if re.search(r"(?<![\w.])" + old + r"(?![\w])", base):
                for new in GROUPS:
                    if new != old: add(_replace_word(base, old, new), base, f"group {old}→{new}")
        add(f"-({base})", base, "sign inversion")
        for wrapper in ("rank", "zscore", "scale"):
            add(f"{wrapper}({base})", base, f"normalize with {wrapper}")
        for op in ("ts_mean", "ts_median"):
            for window in (5, 20, 60, 120, 252):
                add(f"{op}({base}, {window})", base, f"smooth with {op} over {window}")
        for op in ("ts_delta", "ts_av_diff"):
            for window in (5, 20, 60, 120, 252):
                add(f"{op}({base}, {window})", base, f"difference with {op} over {window}")
        try:
            root = V.parse(base)
            if isinstance(root, V.Call):
                positional = [x for x in root.args if not isinstance(x, V.KwArg)]
                if positional: add(_serialize(positional[0]), base, f"simplify by removing {root.name}")
        except Exception: pass
        if len(out) >= limit: break
    return out


def _build_unique_population(parents: list[str], fields: list[dict], size: int, offset: int,
                             evaluated: set[str], max_ops: int, categories: dict,
                             direction: dict, rng: random.Random) -> list[dict]:
    """Fill a generation with validated, unseen and structurally distinct expressions."""
    allowed = set(direction.get("allowed_mutations", MUTATION_METHODS))
    vector_ops = _vector_reducers(direction.get("operator_atlas", []))
    if "field" in allowed:
        coverage_size = min(size, max(len(parents), min(len(fields), max(2, math.ceil(size / 3)))))
        raw = _coverage_population(parents, fields, coverage_size, offset, vector_ops)
    else:
        raw = [{"expression": x, "parent": "", "mutation": "seed"} for x in parents]
    systematic_fields = fields
    if "field" not in allowed:
        used_ids = {fid for expr in parents for fid in gen._fields_in(expr, {f.get("id") for f in fields})}
        systematic_fields = [f for f in fields if f.get("id") in used_ids]
    systematic = _systematic_variants(parents, systematic_fields, max(size * 30, 300), vector_ops,
        direction.get("windows") or WINDOWS)
    atlas = direction.get("operator_atlas") or []
    if "atlas" in allowed and atlas:
        atlas_rows = _atlas_variants(parents, atlas, direction.get("windows") or WINDOWS,
            max(size * 30, 900))
        atlas_buckets: dict[str, list[dict]] = {}
        for row in atlas_rows:
            family = row["mutation"].split(":", 1)[0]
            atlas_buckets.setdefault(family, []).append(row)
        diverse_atlas: list[dict] = []
        while any(atlas_buckets.values()):
            for family in sorted(atlas_buckets):
                if atlas_buckets[family]: diverse_atlas.append(atlas_buckets[family].pop(0))
        atlas_rows = diverse_atlas
        if atlas_rows:
            pivot = offset % len(atlas_rows); atlas_rows = atlas_rows[pivot:] + atlas_rows[:pivot]
        systematic.extend(atlas_rows)
    if "parameter" in allowed and atlas:
        systematic.extend(_parameter_variants(parents, atlas, max(size * 20, 200)))
    def method(row):
        text = row["mutation"]
        if text.startswith("field "): return "field"
        if text.startswith("horizon "): return "horizon"
        if text.startswith("operator "): return "operator"
        if text.startswith("atlas "): return "atlas"
        if text.startswith("parameter "): return "parameter"
        if text.startswith("group "): return "group"
        if text.startswith("sign "): return "sign"
        if text.startswith("normalize "): return "normalize"
        if text.startswith("smooth "): return "smooth"
        if text.startswith("difference "): return "difference"
        if text.startswith("simplify "): return "simplify"
        return "seed"
    systematic = [x for x in systematic if method(x) == "seed" or method(x) in allowed]
    if "crossover" in allowed and len(parents) > 1:
        for i, left in enumerate(parents):
            for right in parents[i + 1:]:
                for seed in range(3):
                    child,mutation=_typed_crossover(left,right,fields,random.Random(offset+i*31+seed))
                    if child!=left: systematic.append({"expression":child,"parent":left,"mutation":mutation})
    # Interleave method families. A large selected field pool must not crowd every
    # structural method out of the candidate target.
    buckets: dict[str, list[dict]] = {}
    for row in systematic: buckets.setdefault(method(row) if not row["mutation"].startswith("crossover") else "crossover", []).append(row)
    ordered: list[dict] = []
    while any(buckets.values()):
        for name in direction.get("allowed_mutations", list(MUTATION_METHODS)):
            quota = 4 if name == "atlas" else 2 if name in {"operator", "parameter"} else 1
            for _ in range(quota):
                if buckets.get(name): ordered.append(buckets[name].pop(0))
        if buckets.get("seed"): ordered.append(buckets["seed"].pop(0))
    raw.extend(ordered)
    diagnostics = candidate_diagnostics(raw, fields, max_ops, categories, direction)
    valid = diagnostics["accepted"]
    ledger = direction.setdefault("rejection_ledger", [])
    ledger.extend(diagnostics["rejected"][:2000])
    chosen, seen = [], {expression_hash(x) for x in evaluated}
    for row in valid:
        key = expression_hash(row["expression"])
        if key not in seen:
            row["expression"] = canonical(row["expression"]); chosen.append(row); seen.add(key)
        if len(chosen) >= size: return chosen
    attempts = 0
    frontier = [x["expression"] for x in valid] or list(parents)
    while len(chosen) < size and attempts < size * 100 and frontier:
        attempts += 1
        parent = rng.choice(frontier); mate = rng.choice(frontier)
        child, mutation = _mutate(parent, fields, rng, mate, direction)
        try: key = expression_hash(child)
        except Exception: continue
        if key in seen: continue
        row = {"expression": child, "parent": parent, "mutation": mutation}
        detail = candidate_diagnostics([row], fields, max_ops, categories, direction)
        checked = detail["accepted"]
        ledger.extend(detail["rejected"])
        if checked:
            checked[0]["expression"] = canonical(child)
            chosen.append(checked[0]); frontier.append(child); seen.add(key)
    return chosen


def _rejection_reason(expression: str, verdict: dict, fields: list, categories: dict | None,
                      direction: dict | None) -> str:
    """Return one stable, user-facing reason for a discarded genetic candidate."""
    issues = [str(x).lower() for x in verdict.get("issues", [])]
    joined = " ".join(issues)
    if "parse" in joined or "syntax" in joined or "parenthes" in joined: return "syntax"
    if "keyword" in joined: return "missing or invalid keyword"
    if "argument" in joined or "arity" in joined or "parameter" in joined: return "arity"
    if "vector" in joined or "matrix" in joined or "type" in joined: return "datatype"
    if "operator" in joined: return "operator"
    ids = {f.get("id") for f in fields}
    used = set(gen._fields_in(expression, ids))
    max_fields = int((direction or {}).get("max_fields_per_expression", 2))
    if not used or len(used) > max_fields: return "field count"
    if categories and len({categories.get(x) for x in used if categories.get(x)}) > 2:
        return "category limit"
    if _structurally_redundant(expression): return "duplication or redundant structure"
    return "validation"


def candidate_diagnostics(candidates: list[dict], fields: list, max_ops: int,
                          categories: dict | None = None, direction: dict | None = None) -> dict:
    """Validate candidates while preserving an auditable rejection ledger."""
    if not candidates: return {"accepted": [], "rejected": [], "counts": {}}
    multi = any(len(set(gen._fields_in(c["expression"], {f.get("id") for f in fields}))) > 1 for c in candidates)
    checked = gen.validate_expressions([c["expression"] for c in candidates], fields, max_ops, multi)
    accepted, rejected, counts = [], [], {}
    for candidate, verdict in zip(candidates, checked["results"]):
        reason = _rejection_reason(candidate["expression"], verdict, fields, categories, direction)
        valid = bool(verdict.get("ok")) and reason not in {"field count", "category limit", "duplication or redundant structure"}
        if valid:
            accepted.append(candidate)
        else:
            counts[reason] = counts.get(reason, 0) + 1
            rejected.append({**candidate, "reason": reason, "issues": verdict.get("issues", [])})
    return {"accepted": accepted, "rejected": rejected, "counts": counts}


def _valid_candidates(candidates: list[dict], fields: list, max_ops: int, categories: dict | None = None,
                      direction: dict | None = None) -> list[dict]:
    return candidate_diagnostics(candidates, fields, max_ops, categories, direction)["accepted"]


def _structurally_redundant(expression: str) -> bool:
    """Reject algebraic/idempotent shapes that pass syntax checks but add no information."""
    try: root = V.parse(expression)
    except Exception: return True
    stack = [root]
    idempotent = {"rank", "zscore", "scale"}
    while stack:
        node = stack.pop()
        if isinstance(node, V.Call):
            pos = [x for x in node.args if not isinstance(x, V.KwArg)]
            if node.name in idempotent and pos and isinstance(pos[0], V.Call) and pos[0].name == node.name:
                return True
            if node.name in {"add", "subtract", "multiply", "divide"} and len(pos) >= 2 and repr(pos[0]) == repr(pos[1]):
                return True
            stack.extend(node.args)
        elif isinstance(node, V.KwArg): stack.append(node.value)
        elif isinstance(node, V.Unary): stack.append(node.operand)
        elif isinstance(node, V.BinOp):
            if node.op in {"-", "/"} and repr(node.left) == repr(node.right): return True
            stack.extend([node.left, node.right])
        elif isinstance(node, V.Seq): stack.extend(node.statements)
    return False


def _complexity(expression: str) -> int:
    try: return V._count_calls(V.parse(expression), ignore_prefixes=("vec_",))
    except Exception: return 999


def _aggregate(rows: list[dict], expr: str, parent: str, mutation: str) -> dict:
    rr = [r for r in rows if r.get("expr") == expr]
    def vals(k, absolute=False): return [(abs(float(r[k])) if absolute else float(r[k])) for r in rr if r.get(k) is not None]
    mean = lambda xs: sum(xs) / len(xs) if xs else 0.0
    sharpe, fitness = vals("sharpe"), vals("fitness")
    turnover, returns, margin, drawdown = vals("turnover", True), vals("returns"), vals("margin"), vals("drawdown", True)
    corr = vals("prod_corr", True) + vals("self_corr", True)
    complexity = _complexity(expr)
    stability = 1.0 / (1.0 + (max(fitness) - min(fitness) if len(fitness) > 1 else 0.0))
    return {"expression": expr, "parent": parent, "mutation": mutation,
            "sharpe": mean(sharpe), "fitness": mean(fitness), "turnover": mean(turnover),
            "returns": mean(returns), "margin": mean(margin), "drawdown": mean(drawdown), "stability": stability,
            "correlation": max(corr) if corr else 0.0,
            "complexity": complexity, "passed_configs": sum(1 for r in rr if r.get("passed")),
            "configs": len(rr), "alpha_ids": [r.get("alpha_id") for r in rr if r.get("alpha_id")]}


def _novelty(expr: str, population: list[dict]) -> float:
    a = set(gen._operators_in(expr) + gen._leaf_idents(expr))
    others = [set(gen._operators_in(x["expression"]) + gen._leaf_idents(x["expression"])) for x in population if x["expression"] != expr]
    if not others: return 1.0
    sims = [len(a & b) / max(1, len(a | b)) for b in others]
    return 1.0 - max(sims)


def _dominates(a: dict, b: dict) -> bool:
    maximize = ("sharpe", "fitness", "returns", "margin", "stability", "novelty")
    minimize = ("turnover", "drawdown", "correlation", "complexity")
    no_worse = all(a[k] >= b[k] for k in maximize) and all(a[k] <= b[k] for k in minimize)
    better = any(a[k] > b[k] for k in maximize) or any(a[k] < b[k] for k in minimize)
    return no_worse and better


def _pareto(pop: list[dict]) -> list[dict]:
    remaining = list(pop); ranked = []; rank = 0
    while remaining:
        front = [x for x in remaining if not any(_dominates(y, x) for y in remaining if y is not x)]
        for x in front: x["pareto_rank"] = rank
        ranked.extend(front); remaining = [x for x in remaining if x not in front]; rank += 1
    keys = ("fitness", "sharpe", "stability", "novelty", "turnover", "drawdown", "correlation", "complexity")
    bounds = {k: (min(float(x.get(k, 0)) for x in ranked), max(float(x.get(k, 0)) for x in ranked)) for k in keys}
    def norm(x, k):
        lo, hi = bounds[k]
        return 0.5 if hi == lo else (float(x.get(k, 0)) - lo) / (hi - lo)
    for x in ranked:
        x["selection_score"] = (2 * norm(x, "fitness") + norm(x, "sharpe") + norm(x, "stability") + norm(x, "novelty")
                                - norm(x, "turnover") - norm(x, "drawdown") - norm(x, "correlation") - norm(x, "complexity"))
    return sorted(ranked, key=lambda x: (x["pareto_rank"], -x["selection_score"]))


def _save_generation(run_id: int, generation: int, pop: list[dict], survivors: set[str]):
    with SessionLocal() as db:
        db.execute(delete(M.EvolutionIndividual).where(
            M.EvolutionIndividual.run_id == run_id,
            M.EvolutionIndividual.generation == generation))
        for x in pop:
            db.add(M.EvolutionIndividual(run_id=run_id, generation=generation, expression=x["expression"],
                    parent_expression=x.get("parent", ""), mutation=x.get("mutation", ""),
                    metrics_json=json.dumps(x), pareto_rank=x.get("pareto_rank", 999),
                    survived=x["expression"] in survivors))
        db.commit()


def _genetic_settings(candidate: dict, protocol: dict, lineage: dict, ordinal: int) -> dict:
    """Choose a reproducible settings gene for an expression descendant.

    The first generation deliberately covers the available settings space. Later
    descendants inherit their parent's settings and mutate one gene, so selection
    can carry a useful configuration forward without exposing a settings maze in UI.
    """
    direction = protocol.get("direction") or {}
    universes = list(protocol.get("universes") or [protocol.get("universe", "TOP3000")])
    neutralizations = list(protocol.get("neutralizations") or ["INDUSTRY"])
    decays = list(direction.get("decay_values") or [protocol.get("decay", 4)])
    truncations = list(direction.get("truncation_values") or [protocol.get("truncation", .08)])
    parent = lineage.get(candidate.get("parent") or "", {}).get("settings")
    if parent:
        settings = dict(parent)
        gene = ordinal % 4
        pools = [universes, neutralizations, decays, truncations]
        names = ["universe", "neutralization", "decay", "truncation"]
        pool = pools[gene]
        current = settings.get(names[gene])
        choices = [x for x in pool if x != current]
        if choices: settings[names[gene]] = choices[(ordinal // 4) % len(choices)]
        return settings
    return {"universe": universes[ordinal % len(universes)],
            "neutralization": neutralizations[(ordinal * 2 + ordinal // 3) % len(neutralizations)],
            "decay": decays[(ordinal * 3 + ordinal // 5) % len(decays)],
            "truncation": truncations[(ordinal * 5 + ordinal // 7) % len(truncations)]}


def run_evolution(*, run_id: int, protocol: dict, progress, should_cancel) -> dict:
    """Run a deterministic search with pilot evaluation and preset-based promotion."""
    random_seed = int(protocol.get("random_seed", run_id))
    rng = random.Random(random_seed)
    saved: dict = {}
    with SessionLocal() as db:
        row = db.get(M.EvolutionRun, run_id)
        if row:
            try: saved = json.loads(row.result_json or "{}").get("checkpoint", {})
            except (TypeError, ValueError): saved = {}
            row.status = "running"; row.updated_at = time.time(); db.commit()

    fields = list(protocol["fields"]); max_ops = protocol["max_operators"]
    policy = protocol.get("catalog_policy", "selected")
    if policy != "selected":
        from app.catalog.service import compatible_fields
        expanded = {f.get("id"): f for f in fields}
        for base in list(fields):
            for field in compatible_fields(base.get("id"), protocol["instrument"], protocol["region"],
                    protocol["delay"], protocol["universes"][0], policy,
                    protocol.get("max_catalog_fields", 40)):
                expanded.setdefault(field["id"], field)
        fields = list(expanded.values())[:len(protocol["fields"]) + protocol.get("max_catalog_fields", 40)]

    categories = dict(protocol.get("categories") or {})
    categories.update({f["id"]: f.get("category", "") for f in fields if f.get("category")})
    protocol["categories"] = categories; protocol["effective_field_count"] = len(fields)
    seeds = list(dict.fromkeys(e.strip() for e in protocol["expressions"] if e.strip()))
    checked = gen.validate_expressions(seeds, fields, max_ops, multi_field=True)
    max_fields = int((protocol.get("direction") or {}).get("max_fields_per_expression", 2))
    field_ids = {f.get("id") for f in fields}
    checked["results"] = [x for x in checked["results"]
        if 1 <= len(set(gen._fields_in(x["expr"], field_ids))) <= max_fields]
    seeds = [canonical(x["expr"]) for x in checked["results"] if x["ok"]]
    if not seeds:
        raise RuntimeError("None of the seed expressions passed validation with the selected fields.")

    pop_size = protocol["population_size"]; elite_n = min(protocol["elite_size"], pop_size)
    candidate_target = int(protocol.get("candidate_count", 30)); budget = int(protocol["max_evaluations"])
    universes = list(protocol["universes"]); neutralizations = list(protocol["neutralizations"])
    pilot_universe, pilot_neutralization = universes[0], neutralizations[0]
    evaluated: set[str] = set(saved.get("evaluated", [])); parents = saved.get("parents") or seeds
    used_budget = int(saved.get("used_budget", 0)); history: list[dict] = saved.get("history", [])
    archive: dict[str, dict] = saved.get("archive", {}); evidence: dict[str, list[dict]] = saved.get("evidence", {})
    lineage: dict[str, dict] = saved.get("lineage", {}); final: list[dict] = saved.get("final", [])
    start_generation = int(saved.get("next_generation", 0))
    if saved.get("rng_state"):
        rng.setstate(_tuple_tree(saved["rng_state"]))

    def simulate(expressions: list[str], universe: str, neutralization: str,
                 decay: int | None = None, truncation: float | None = None) -> list[dict]:
        nonlocal used_budget
        if not expressions: return []
        result = sim.run_simulation(expressions=expressions, region=protocol["region"],
            delay=protocol["delay"], instrument=protocol["instrument"], universes=[universe],
            neutralizations=[neutralization], decay=protocol["decay"] if decay is None else decay,
            truncation=protocol["truncation"] if truncation is None else truncation,
            test_period="P0Y", pasteurization="ON", unit_handling="VERIFY", nan_handling="OFF",
            max_trade="OFF", visualization=False, concurrency=protocol["concurrency"], limit_of_multi=10,
            max_turnover=.70, min_sharpe=sim.gate_thresholds(protocol["delay"])["sharpe"],
            min_fitness=sim.gate_thresholds(protocol["delay"])["fitness"], max_corr=.70, tag="",
            winner_tag="", winner_color="GREEN", tag_winners_above=1.0,
            check_submission=protocol.get("correlation_checks", False), get_pnl=False, get_stats=False,
            progress=lambda **k: None, should_cancel=should_cancel)
        used_budget += int(result.get("configs", len(expressions)))
        return result.get("results", [])

    try:
        planned_population = list(protocol.get("planned_candidates") or [])
        planned_generations = min(50, max(protocol["generations"], math.ceil(candidate_target / pop_size)))
        for generation in range(start_generation, planned_generations):
            if should_cancel() or len(evaluated) >= candidate_target: break
            wanted = min(pop_size, candidate_target - len(evaluated))
            if planned_population:
                candidates = [dict(x) for x in planned_population
                    if int(x.get("generation", 1)) == generation + 1 and x["expression"] not in evaluated][:wanted]
            else:
                candidates = _build_unique_population(parents, fields, wanted, generation * pop_size,
                    evaluated, max_ops, categories, protocol.get("direction") or {}, rng)
            candidates = [c for c in candidates if c["expression"] not in evaluated][:wanted]
            if not candidates: break
            for index, candidate in enumerate(candidates):
                candidate["settings"] = _genetic_settings(candidate, protocol, lineage, len(evaluated) + index)
            rejected_total = len((protocol.get("direction") or {}).get("rejection_ledger") or [])
            progress(total=budget, done=used_budget,
                message=f"Generation {generation + 1}/{planned_generations}: simulating {len(candidates)} accepted candidates",
                generation=generation + 1, generation_total=planned_generations,
                target_candidates=candidate_target, generated_candidates=len(evaluated) + len(candidates),
                accepted_candidates=len(candidates), rejected_candidates=rejected_total,
                simulated_candidates=len(evaluated))
            rows = []
            setting_groups: dict[tuple, list[str]] = {}
            for candidate in candidates:
                settings = candidate["settings"]
                key = (settings["universe"], settings["neutralization"], int(settings["decay"]), float(settings["truncation"]))
                setting_groups.setdefault(key, []).append(candidate["expression"])
            for (universe, neutralization, decay, truncation), expressions in setting_groups.items():
                rows.extend(simulate(expressions, universe, neutralization, decay, truncation))
            for candidate in candidates:
                expr = candidate["expression"]
                evaluated.add(expr); evidence.setdefault(expr, []).extend([r for r in rows if r.get("expr") == expr])
                lineage[expr] = candidate
                archive[expr] = _aggregate(evidence[expr], expr, candidate["parent"], candidate["mutation"])
                archive[expr]["settings"] = candidate["settings"]
            combined = list(archive.values())
            for item in combined: item["novelty"] = _novelty(item["expression"], combined)
            final = _pareto(combined); parents = [x["expression"] for x in final[:elite_n]]
            scored = [archive[c["expression"]] for c in candidates]
            _save_generation(run_id, generation, scored, set(parents))
            history.append({"generation": generation + 1, "stage": "pilot", "evaluated": len(scored),
                            "survivors": final[:elite_n]})
            _checkpoint(run_id, {"phase": "pilot", "next_generation": generation + 1,
                "evaluated": sorted(evaluated), "parents": parents, "used_budget": used_budget,
                "history": history, "archive": archive, "evidence": evidence, "lineage": lineage,
                "final": final, "rng_state": rng.getstate()})
            progress(total=budget, done=used_budget,
                log=f"generation {generation + 1}: {len(scored)} new; {len(evaluated)}/{candidate_target} unique candidates",
                generation=generation + 1, generation_total=planned_generations,
                target_candidates=candidate_target, generated_candidates=len(evaluated),
                accepted_candidates=len(scored), rejected_candidates=rejected_total,
                simulated_candidates=len(evaluated))

        preset = PRESETS.get(protocol.get("preset", "balanced"), PRESETS["balanced"])
        remaining_configs = [(u, n) for u in universes for n in neutralizations
                             if (u, n) != (pilot_universe, pilot_neutralization)]
        remaining_configs = remaining_configs[:int(protocol.get("robustness_config_limit", len(remaining_configs)))]
        promote_n = 0
        if remaining_configs and preset["robustness"] == "promote":
            promote_n = max(elite_n, math.ceil(len(evaluated) / 3))
        elif remaining_configs and preset["robustness"] == "full":
            promote_n = max(elite_n, math.ceil(len(evaluated) / 2))
        promoted = [x["expression"] for x in final[:min(len(final), promote_n)]]
        robustness_start = int(saved.get("robustness_index", 0)) if saved.get("phase") == "robustness" else 0
        robustness_done = robustness_start
        for index, (universe, neutralization) in enumerate(remaining_configs):
            if index < robustness_start: continue
            if should_cancel(): break
            progress(total=budget, done=used_budget,
                message=f"Robustness {index + 1}/{len(remaining_configs)}: {universe} · {neutralization}")
            rows = []
            promoted_groups: dict[tuple, list[str]] = {}
            for expr in promoted:
                inherited = lineage.get(expr, {}).get("settings") or {}
                key = (int(inherited.get("decay", protocol["decay"])), float(inherited.get("truncation", protocol["truncation"])))
                promoted_groups.setdefault(key, []).append(expr)
            for (decay, truncation), expressions in promoted_groups.items():
                rows.extend(simulate(expressions, universe, neutralization, decay, truncation))
            for expr in promoted:
                evidence.setdefault(expr, []).extend([r for r in rows if r.get("expr") == expr])
                meta = lineage[expr]
                archive[expr] = _aggregate(evidence[expr], expr, meta["parent"], meta["mutation"])
                archive[expr]["settings"] = meta.get("settings", {})
            _checkpoint(run_id, {"phase": "robustness", "next_generation": planned_generations,
                "robustness_index": index + 1, "evaluated": sorted(evaluated), "parents": parents,
                "used_budget": used_budget, "history": history, "archive": archive,
                "evidence": evidence, "lineage": lineage, "final": final, "rng_state": rng.getstate()})
            robustness_done = index + 1

        combined = list(archive.values())
        for item in combined: item["novelty"] = _novelty(item["expression"], combined)
        final = _pareto(combined) if combined else []
        robustness = None
        if protocol.get("final_walk_forward") and final:
            ids = {f.get("id") for f in fields if f.get("id")}
            _, d0, u0 = engine.valid_combo(protocol["instrument"], protocol["region"], 0, "TOP3000")
            _, d1, u1 = engine.valid_combo(protocol["instrument"], protocol["region"], 1, "TOP3000")
            common = ids if d0 == 0 and d1 == 1 else set()
            if common:
                common &= engine.datafields_present(protocol["region"], 0, u0, protocol["instrument"], ids)
                common &= engine.datafields_present(protocol["region"], 1, u1, protocol["instrument"], ids)
            if common == ids:
                robustness = sim.run_walk_forward(expressions=[x["expression"] for x in final[:elite_n]],
                    region=protocol["region"], instrument=protocol["instrument"],
                    neutralizations=protocol["neutralizations"], decay=protocol["decay"],
                    truncation=protocol["truncation"], concurrency=protocol["concurrency"], limit_of_multi=10,
                    tag="", winner_tag="", progress=lambda **k: None, should_cancel=should_cancel)
            else:
                robustness = {"skipped": "delay 0 and delay 1 do not share every selected datafield"}

        survivor_ids = {x["expression"] for x in final[:elite_n]}
        promoted_ids = set(promoted)
        for item in archive.values():
            item["stage"] = "robustness" if item["expression"] in promoted_ids else "pilot"
            item["status"] = "survivor" if item["expression"] in survivor_ids else "evaluated"
            item["expression_hash"] = expression_hash(item["expression"])
        evolved = sorted(archive.values(), key=lambda x: (x.get("pareto_rank", 999), -x.get("selection_score", 0)))
        field_coverage = {f.get("id"): 0 for f in fields if f.get("id")}
        operator_coverage: dict[str, int] = {}
        for item in evolved:
            for fid in set(gen._fields_in(item["expression"], set(field_coverage))): field_coverage[fid] += 1
            for op in gen._operators_in(item["expression"]): operator_coverage[op] = operator_coverage.get(op, 0) + 1
        rejections = list((protocol.get("direction") or {}).get("rejection_ledger") or [])
        rejection_counts: dict[str, int] = {}
        for item in rejections: rejection_counts[item.get("reason", "validation")] = rejection_counts.get(item.get("reason", "validation"), 0) + 1
        out = {"run_id": run_id, "hypothesis": protocol.get("hypothesis", ""), "evaluations": used_budget,
               "evaluated_expressions": len(evaluated), "target_expressions": candidate_target,
               "budget": budget, "generations": history, "pareto_survivors": final[:elite_n],
               "evolved_expressions": evolved, "search_exhausted": len(evaluated) < candidate_target and not should_cancel(),
               "promotion": {"pilot": len(evaluated), "promoted": len(promoted),
                             "additional_configurations": len(remaining_configs)},
               "coverage": {"fields": field_coverage, "operators": operator_coverage},
               "rejections": {"total": len(rejections), "counts": rejection_counts,
                              "items": rejections[:500]},
               "robustness": robustness, "cancelled": should_cancel(), "random_seed": random_seed,
               "checkpoint": {"phase": "robustness" if should_cancel() and len(evaluated) >= candidate_target else ("pilot" if should_cancel() else "complete"),
                   "next_generation": planned_generations if len(evaluated) >= candidate_target else len(history),
                   "robustness_index": robustness_done, "evaluated": sorted(evaluated), "parents": parents,
                   "used_budget": used_budget, "history": history, "archive": archive,
                   "evidence": evidence, "lineage": lineage, "final": final, "rng_state": rng.getstate()},
               "reproducibility": {"random_seed": random_seed,
                   "seed_hashes": [expression_hash(x) for x in seeds], "fields": fields,
                   "operator_reference": "runtime", "protocol": protocol}}
        with SessionLocal() as db:
            row = db.get(M.EvolutionRun, run_id)
            row.status = "cancelled" if should_cancel() else "done"; row.updated_at = time.time()
            row.result_json = json.dumps(out); db.commit()
        return out
    except Exception:
        with SessionLocal() as db:
            row = db.get(M.EvolutionRun, run_id)
            if row: row.status = "error"; row.updated_at = time.time(); db.commit()
        raise

def list_runs(limit=30) -> list:
    with SessionLocal() as db:
        rows = db.scalars(select(M.EvolutionRun).order_by(desc(M.EvolutionRun.created_at)).limit(limit)).all()
        return [{"id": r.id, "created_at": r.created_at, "status": r.status, "hypothesis": r.hypothesis,
                 "protocol": json.loads(r.protocol_json or "{}"), "result": json.loads(r.result_json or "{}")} for r in rows]


def get_run(run_id: int) -> dict | None:
    with SessionLocal() as db:
        run = db.get(M.EvolutionRun, run_id)
        if not run: return None
        inds = db.scalars(select(M.EvolutionIndividual).where(M.EvolutionIndividual.run_id == run_id)
                          .order_by(M.EvolutionIndividual.generation, M.EvolutionIndividual.pareto_rank)).all()
        return {"id": run.id, "status": run.status, "hypothesis": run.hypothesis,
                "protocol": json.loads(run.protocol_json or "{}"), "result": json.loads(run.result_json or "{}"),
                "individuals": [{"generation": x.generation, "expression": x.expression,
                    "parent": x.parent_expression, "mutation": x.mutation, "pareto_rank": x.pareto_rank,
                    "survived": x.survived, "metrics": json.loads(x.metrics_json or "{}")} for x in inds]}
