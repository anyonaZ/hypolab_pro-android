from app.generation.service import expand_templates


def test_vector_reducers_apply_independently_to_both_template_slots():
    fields = [
        {"id": "matrix_a", "type": "MATRIX"},
        {"id": "vector_b", "type": "VECTOR"},
    ]
    out = expand_templates(
        ["add({field}, {field2})"], ["matrix_a"], fields,
        ["vec_avg", "vec_max"], max_operators=4, multi_field=True,
        max_combos=20, field2_ids=["vector_b"],
    )
    assert "add(matrix_a, vec_avg(vector_b))" in out["expressions"]
    assert "add(matrix_a, vec_max(vector_b))" in out["expressions"]


def test_custom_unknown_field_is_not_assumed_to_be_vector():
    fields = [{"id": "matrix_a", "type": "MATRIX"}, {"id": "custom_x", "type": "MATRIX"}]
    out = expand_templates(
        ["add({field}, {field2})"], ["matrix_a"], fields,
        ["vec_avg", "vec_max"], max_operators=4, multi_field=True,
        max_combos=20, field2_ids=["custom_x"],
    )
    assert out["expressions"] == ["add(matrix_a, custom_x)"]
