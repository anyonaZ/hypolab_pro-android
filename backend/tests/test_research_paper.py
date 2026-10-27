from app.research import service


def test_research_prompt_keeps_complete_paper_and_all_fields():
    paper = "opening\n" + ("evidence " * 7000) + "TAIL_MECHANISM"
    fields = [
        {"id": f"field_{i}", "type": "MATRIX", "description": f"complete description {i}"}
        for i in range(55)
    ]
    prompt = service._prompt(
        "fundamental", "USA", 1, "EQUITY", ["dataset"], fields, {}, "goal",
        paper, False, "single", 4, 5,
    )
    assert "TAIL_MECHANISM" in prompt
    assert "field_54" in prompt
    assert "COMPLETE RESEARCH PAPER" in prompt


def test_page_parser_defaults_to_all_pages():
    assert service._page_set("", 12) is None
    assert service._page_set("1-3,7", 12) == {1, 2, 3, 7}
