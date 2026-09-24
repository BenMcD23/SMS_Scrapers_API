"""The deterministic bits around the texts prompt — no network, `generate` is stubbed."""

import texts.ai as ai


def _stub(monkeypatch, output: str) -> list[str]:
    prompts: list[str] = []

    def fake_generate(prompt, *_args, **_kwargs):
        prompts.append(prompt)
        return output, "stub"

    monkeypatch.setattr(ai, "generate", fake_generate)
    return prompts


def test_c_flight_gets_uniform_line(monkeypatch):
    _stub(monkeypatch, "===MAIN===\nDrill!\n\n===C===\nMap Reading 1 and Drill")
    main, c, _ = ai.generate_message("1st Period\nBoth Flights:\nDrill", "1st Period:\nMap Reading 1")
    assert main == "Drill!"
    assert c == "Uniform - Civvies\n\nMap Reading 1 and Drill"


def test_no_c_flight_section_when_nothing_to_say(monkeypatch):
    _stub(monkeypatch, "===MAIN===\nDrill!\n\n===C===\nNONE")
    assert ai.generate_message("x", "1st Period:\nAwaiting Intake")[1] == ""
    # Empty programme: blank even if the model writes something anyway.
    _stub(monkeypatch, "===MAIN===\nDrill!\n\n===C===\nSee you on parade!")
    assert ai.generate_message("x", "  ")[1] == ""


def test_bare_uniform_expanded_before_prompting(monkeypatch):
    prompts = _stub(monkeypatch, "===MAIN===\nx\n\n===C===\nNONE")
    ai.generate_message("Uniform / Radio\nUniform Prep", "Uniform (MTP)")
    assert "Uniform Maintenance / Radio\nUniform Prep" in prompts[0]
    assert "Uniform Maintenance (MTP)" in prompts[0]

