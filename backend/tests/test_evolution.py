from app.evolution import service


FIELDS = [
    {"id": "field_a", "type": "MATRIX", "category": "fundamental", "dataset_id": "ds1"},
    {"id": "field_b", "type": "MATRIX", "category": "fundamental", "dataset_id": "ds2"},
    {"id": "field_c", "type": "VECTOR", "category": "analyst", "dataset_id": "ds3"},
]


def test_canonical_hash_ignores_formatting():
    assert service.expression_hash("rank( field_a )") == service.expression_hash("rank(field_a)")


def test_preview_reports_staged_cost_exactly():
    protocol = {
        "preset": "balanced", "candidate_count": 30, "expressions": ["rank(field_a)"],
        "fields": FIELDS, "universes": ["TOP3000", "TOP1000"],
        "neutralizations": ["INDUSTRY", "SECTOR"],
    }
    report = service.preview(protocol)
    assert report["pilot_configurations"] == 30
    assert report["promoted_candidates"] == 10
    assert report["max_configurations"] == 60  # 30 pilot + 10 across three remaining configs


def test_preview_explains_two_field_dataset_and_category_relationship():
    protocol = {"preset": "balanced", "candidate_count": 10,
        "expressions": ["add(rank(field_a), rank(field_b))"], "fields": FIELDS,
        "universes": ["TOP3000"], "neutralizations": ["INDUSTRY"]}
    report = service.preview(protocol)
    assert report["seed_profiles"][0]["relationship"] == "different datasets, same category"


def test_preview_generates_full_review_population_without_simulation(monkeypatch):
    monkeypatch.setattr(service.sim, "run_simulation", lambda **kwargs: (_ for _ in ()).throw(
        AssertionError("preview must not simulate")))
    protocol = {"preset": "balanced", "candidate_count": 24, "random_seed": 44,
        "expressions": ["rank(ts_delta(field_a, 20))", "zscore(ts_mean(field_b, 60))"],
        "fields": FIELDS, "categories": {x["id"]: x["category"] for x in FIELDS},
        "direction": {"allowed_mutations": list(service.MUTATION_METHODS)},
        "universes": ["TOP3000"], "neutralizations": ["INDUSTRY"]}
    report = service.preview(protocol)
    assert report["generated_count"] == 24
    assert len({x["expression_hash"] for x in report["generated_candidates"]}) == 24
    assert all(x["status"] == "generated" for x in report["generated_candidates"])


def test_atlas_variants_cover_families_lookbacks_and_keyword_parameters():
    atlas = [
        {"name": "ts_rank", "family": "time-series", "params": [
            {"name": "x", "kind": "positional", "required": True},
            {"name": "d", "kind": "positional", "required": True},
            {"name": "constant", "kind": "keyword", "required": False}]},
        {"name": "group_zscore", "family": "group", "params": [
            {"name": "x", "kind": "positional", "required": True},
            {"name": "group", "kind": "positional", "required": True}]},
        {"name": "winsorize", "family": "cross-sectional", "params": [
            {"name": "x", "kind": "positional", "required": True},
            {"name": "std", "kind": "keyword", "required": False}]},
        {"name": "regression_neut", "family": "regression", "params": [
            {"name": "y", "kind": "positional", "required": True},
            {"name": "x", "kind": "positional", "required": True}]},
    ]
    rows = service._atlas_variants(["rank(field_a)", "rank(field_b)"], atlas, [5, 20], 200)
    expressions = {x["expression"] for x in rows}
    assert "ts_rank(rank(field_a), 20, constant=1)" in expressions
    assert "group_zscore(rank(field_a), industry)" in expressions
    assert "winsorize(rank(field_a), std=4)" in expressions
    assert "regression_neut(rank(field_a), rank(field_b))" in expressions


def test_required_keyword_arguments_are_always_supplied_together():
    atlas = [{"name": "custom_window", "family": "time-series", "params": [
        {"name": "x", "kind": "positional", "required": True},
        {"name": "d", "kind": "positional", "required": True},
        {"name": "k", "kind": "keyword", "required": True},
        {"name": "dense", "kind": "keyword", "required": True},
    ]}]
    rows = service._atlas_variants(["rank(field_a)"], atlas, [20], 100)
    expressions = [x["expression"] for x in rows]
    assert expressions
    assert all("k=" in x and "dense=" in x for x in expressions)


def test_single_field_mode_uses_pool_but_never_combines_fields():
    direction = {"allowed_mutations": list(service.MUTATION_METHODS),
                 "max_fields_per_expression": 1}
    rows = service._build_unique_population(
        ["rank(ts_delta(field_a, 20))"], FIELDS, 20, 0, set(), 6,
        {x["id"]: x["category"] for x in FIELDS}, direction, service.random.Random(29))
    assert rows
    ids = {x["id"] for x in FIELDS}
    used = [set(service.gen._fields_in(x["expression"], ids)) for x in rows]
    assert all(len(x) == 1 for x in used)
    assert len(set().union(*used)) > 1


def test_fixed_random_seed_reproduces_unique_population():
    args = (["rank(ts_delta(field_a, 20))"], FIELDS, 20, 0, set(), 6,
            {x["id"]: x["category"] for x in FIELDS},
            {"allowed_mutations": ["horizon", "field", "operator", "sign", "group"]})
    first = service._build_unique_population(*args, service.random.Random(731))
    second = service._build_unique_population(*args, service.random.Random(731))
    assert [x["expression"] for x in first] == [x["expression"] for x in second]
    assert len({service.expression_hash(x["expression"]) for x in first}) == len(first)
    assert any("field_b" in x["expression"] for x in first)
    assert any("field_c" in x["expression"] for x in first)


def test_keep_fields_never_substitutes_selected_fields():
    rows = service._build_unique_population(
        ["rank(ts_delta(field_a, 20))"], FIELDS, 16, 0, set(), 6,
        {x["id"]: x["category"] for x in FIELDS},
        {"allowed_mutations": ["horizon", "operator", "sign", "group"]},
        service.random.Random(91),
    )
    assert rows
    assert all("field_b" not in x["expression"] and "field_c" not in x["expression"] for x in rows)


def test_directional_returns_keep_their_sign():
    rows = [{"expr": "rank(field_a)", "sharpe": -1.2, "fitness": -0.5,
             "turnover": 0.2, "returns": -0.08, "margin": -0.01, "drawdown": 0.4}]
    result = service._aggregate(rows, "rank(field_a)", "", "seed")
    assert result["returns"] == -0.08
    assert result["margin"] == -0.01


def test_vector_reducers_exclude_multi_input_and_required_keyword_ops():
    atlas = [
        {"name": "vec_avg", "family": "vector", "params": [
            {"name": "x", "kind": "positional", "required": True}]},
        {"name": "vec_choose", "family": "vector", "params": [
            {"name": "x", "kind": "positional", "required": True},
            {"name": "n", "kind": "positional", "required": True}]},
        {"name": "vec_custom", "family": "vector", "params": [
            {"name": "x", "kind": "positional", "required": True},
            {"name": "mode", "kind": "keyword", "required": True}]},
    ]
    assert service._vector_reducers(atlas) == ["vec_avg"]


def test_settings_genes_are_reproducible_and_cover_each_dimension():
    protocol = {"universe": "TOP3000", "universes": ["TOP3000", "TOP1000"],
        "neutralizations": ["INDUSTRY", "SECTOR"], "decay": 4, "truncation": .08,
        "direction": {"decay_values": [0, 4, 8], "truncation_values": [.03, .08]}}
    rows = [service._genetic_settings({"parent": ""}, protocol, {}, i) for i in range(12)]
    assert rows == [service._genetic_settings({"parent": ""}, protocol, {}, i) for i in range(12)]
    assert {x["universe"] for x in rows} == {"TOP3000", "TOP1000"}
    assert {x["neutralization"] for x in rows} == {"INDUSTRY", "SECTOR"}
    assert {x["decay"] for x in rows} == {0, 4, 8}
    assert {x["truncation"] for x in rows} == {.03, .08}
