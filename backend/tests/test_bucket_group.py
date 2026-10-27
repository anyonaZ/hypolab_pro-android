from app.generation.service import operator_issues


def test_bucket_is_valid_as_computed_group_argument():
    expression = 'group_zscore(close, bucket(rank(volume), range="0,1,0.1"))'
    assert not [issue for issue in operator_issues(expression, {}) if issue[0] in {"BUCKET_NOT_GROUP", "INVALID_GROUP"}]


def test_bucket_is_rejected_as_standalone_signal():
    expression = 'rank(bucket(rank(volume), range="0,1,0.1"))'
    assert any(code == "BUCKET_NOT_GROUP" for code, _ in operator_issues(expression, {}))
