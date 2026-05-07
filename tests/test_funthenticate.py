from __future__ import annotations

import pytest

from funthenticate import (
    DEFAULT_OIDC_SCOPE,
    ConversionStep,
    DrawingPoint,
    FunAuth,
    FunAuthDenied,
    FunMission,
    FunMissionState,
    FunPrompt,
    FunPromptOption,
    InMemoryFunStateStore,
    NumberGuessChallenge,
    NumberGuessMissionState,
    OidcProvider,
    PromptDeck,
    build_conversion_challenge,
    build_conversion_operator_guess_challenge,
    default_conversion_challenge,
    default_fun_auth_ideas,
    default_fun_prompts,
    default_key_drawing_template,
    format_number_for_base,
    google_provider,
    microsoft_entra_provider,
    normalize_drawing,
    render_prompt_card,
)
from funthenticate.cli import main as cli_main
from funthenticate.core import parse_conversion_operator


class FakeRemoteApp:
    def __init__(self, token: dict[str, object] | None = None) -> None:
        self.token = token or {}
        self.redirects: list[dict[str, object]] = []
        self.token_kwargs: dict[str, object] | None = None

    def authorize_redirect(self, redirect_uri: str, **kwargs: object) -> dict[str, object]:
        response = {"redirect_uri": redirect_uri, "kwargs": kwargs}
        self.redirects.append(response)
        return response

    def authorize_access_token(self, **kwargs: object) -> dict[str, object]:
        self.token_kwargs = kwargs
        return self.token


class FakeOAuth:
    def __init__(self) -> None:
        self.clients: dict[str, FakeRemoteApp] = {}
        self.registrations: dict[str, dict[str, object]] = {}

    def register(self, name: str, **kwargs: object) -> FakeRemoteApp:
        self.registrations[name] = kwargs
        client = FakeRemoteApp()
        self.clients[name] = client
        setattr(self, name, client)
        return client

    def create_client(self, name: str) -> FakeRemoteApp | None:
        return self.clients.get(name)


def test_register_provider_builds_authlib_oidc_registration() -> None:
    oauth = FakeOAuth()
    auth = FunAuth(oauth)

    auth.register_provider(
        OidcProvider(
            name="example",
            client_id="client-id",
            client_secret="client-secret",
            server_metadata_url="https://issuer.example/.well-known/openid-configuration",
            client_kwargs={"prompt": "select_account"},
        )
    )

    registration = oauth.registrations["example"]
    assert registration["client_id"] == "client-id"
    assert registration["client_secret"] == "client-secret"
    assert registration["server_metadata_url"] == (
        "https://issuer.example/.well-known/openid-configuration"
    )
    assert registration["client_kwargs"] == {
        "prompt": "select_account",
        "scope": DEFAULT_OIDC_SCOPE,
    }


def test_prepare_answer_and_redirect_flow_requires_successful_custom_prompt() -> None:
    oauth = FakeOAuth()
    oauth.clients["google"] = FakeRemoteApp()
    prompt = FunPrompt(
        key="custom-choice",
        title="Custom Choice",
        prompt="Pick the right option.",
        options=(
            FunPromptOption("yes", "Yes", is_correct=True),
            FunPromptOption("no", "No"),
        ),
        success_message="Correct.",
        failure_message="Try again.",
    )
    auth = FunAuth(oauth, prompt_deck=PromptDeck((prompt,)))
    session: dict[str, object] = {}

    mission = auth.prepare_login(
        session,
        "google",
        next_url="/dashboard",
        prompt_key="custom-choice",
    )
    failed_result = auth.answer_prompt(session, "no")

    assert mission.prompt.key == "custom-choice"
    assert failed_result.passed is False
    with pytest.raises(FunAuthDenied):
        auth.redirect_to_provider(session, "https://app.example/auth/callback")

    passed_result = auth.answer_prompt(session, "yes")
    redirect_response = auth.redirect_to_provider(
        session,
        "https://app.example/auth/callback",
        prompt="select_account",
    )

    assert passed_result.passed is True
    assert redirect_response == {
        "redirect_uri": "https://app.example/auth/callback",
        "kwargs": {"prompt": "select_account"},
    }


def test_prepare_login_rejects_external_next_url() -> None:
    auth = FunAuth(FakeOAuth())
    session: dict[str, object] = {}

    with pytest.raises(FunAuthDenied, match="same-site"):
        auth.prepare_login(
            session,
            "google",
            next_url="https://attacker.example/dashboard",
            prompt_key="authorized-popup",
        )


def test_fun_only_prompt_sequence_can_complete_without_provider_auth() -> None:
    oauth = FakeOAuth()
    prompt = FunPrompt(
        key="custom-choice",
        title="Custom Choice",
        prompt="Pick the right option.",
        options=(
            FunPromptOption("yes", "Yes", is_correct=True),
            FunPromptOption("no", "No"),
        ),
        success_message="Correct.",
        failure_message="Try again.",
    )
    auth = FunAuth(oauth, prompt_deck=PromptDeck((*default_fun_prompts(), prompt)))
    session: dict[str, object] = {}

    mission = auth.prepare_login(
        session,
        next_url="/done",
        prompt_keys=("authorized-popup", "custom-choice"),
    )
    popup_result = auth.answer_popup(session)
    current = auth.current_mission(session)
    assert session["_fun_auth_mission"]["challenge_passed"] is False
    choice_result = auth.answer_prompt(session, "yes")
    fun_result = auth.complete_fun(session)

    assert mission.provider_name is None
    assert mission.prompt.key == "authorized-popup"
    assert mission.prompt_count == 2
    assert popup_result.passed is True
    assert current.prompt.key == "custom-choice"
    assert current.prompt_index == 1
    assert choice_result.passed is True
    assert fun_result.prompt_keys == ("authorized-popup", "custom-choice")
    assert fun_result.next_url == "/done"
    assert session == {}


def test_prepare_mission_is_neutral_alias_and_mission_state_round_trips() -> None:
    auth = FunAuth()
    session: dict[str, object] = {}

    mission = auth.prepare_mission(session, prompt_keys=("authorized-popup",))
    mission_state = FunMissionState.from_mapping(session["_fun_auth_mission"])

    assert isinstance(mission, FunMission)
    assert mission.provider_name is None
    assert mission_state.prompt_keys == ("authorized-popup",)
    assert FunMissionState.from_mapping(mission_state.to_mapping()) == mission_state


def test_fun_only_mission_does_not_redirect_to_provider() -> None:
    auth = FunAuth(FakeOAuth())
    session: dict[str, object] = {}
    auth.prepare_login(session, prompt_key="authorized-popup")
    auth.answer_popup(session)

    with pytest.raises(FunAuthDenied, match="provider auth"):
        auth.redirect_to_provider(session, "https://app.example/auth/callback")


def test_default_first_prompt_is_scale_invariant_drawing_challenge() -> None:
    prompt = default_fun_prompts()[0]
    assert prompt.key == "draw-key"
    assert prompt.drawing_template is not None

    large_same_key = (
        (
            (100.0, 50.0),
            (120.0, 34.0),
            (144.0, 34.0),
            (164.0, 50.0),
            (144.0, 66.0),
            (120.0, 66.0),
            (100.0, 50.0),
        ),
        (
            (164.0, 50.0),
            (256.0, 50.0),
            (256.0, 68.0),
            (274.0, 68.0),
            (274.0, 50.0),
            (294.0, 50.0),
            (294.0, 74.0),
        ),
    )

    result = prompt.drawing_template.compare(large_same_key)

    assert result.passed is True
    assert result.score >= prompt.drawing_template.threshold


def test_drawing_template_rejects_different_form() -> None:
    template = default_key_drawing_template()
    wrong_shape = (
        (
            DrawingPoint(0.0, 0.0),
            DrawingPoint(5.0, 10.0),
            DrawingPoint(10.0, 0.0),
            DrawingPoint(0.0, 0.0),
        ),
    )

    result = template.compare(wrong_shape)

    assert result.passed is False
    assert result.score < template.threshold


def test_normalize_drawing_compares_form_not_canvas_size_or_position() -> None:
    small = normalize_drawing((((0, 0), (10, 0), (10, 10)),), grid_size=32)
    shifted_and_scaled = normalize_drawing(
        (((200, 300), (500, 300), (500, 600)),),
        grid_size=32,
    )

    assert small.pixels == shifted_and_scaled.pixels


def test_answer_drawing_updates_login_mission() -> None:
    oauth = FakeOAuth()
    oauth.clients["google"] = FakeRemoteApp()
    auth = FunAuth(oauth, prompt_deck=PromptDeck(default_fun_prompts()))
    session: dict[str, object] = {}
    auth.prepare_login(session, "google", prompt_key="draw-key")

    result = auth.answer_drawing(session, default_key_drawing_template().strokes)

    assert result.passed is True
    assert session["_fun_auth_mission"]["challenge_passed"] is True
    assert session["_fun_auth_mission"]["drawing_score"] >= result.score


def test_conversion_challenge_reports_each_intermediate_result() -> None:
    challenge = build_conversion_challenge(
        start_value=10,
        steps=(
            ConversionStep("add", 6, output_base=16),
            ConversionStep("multiply", 2, output_base=2),
            ConversionStep("to_oct"),
        ),
    )

    result = challenge.evaluate(["0x10", "31", "0o40"])

    assert result.passed is False
    assert [step.expected for step in result.step_results] == ["0x10", "0b100000", "0o40"]
    assert [step.correct for step in result.step_results] == [True, False, True]
    assert result.correct_count == 2
    assert result.final_value == 32


def test_conversion_challenge_allows_selected_steps_and_prefixless_base_answers() -> None:
    challenge = build_conversion_challenge(
        start_value=13,
        steps=(
            {"kind": "add", "operand": 5, "convert_to": "hex"},
            {"kind": "multiply", "operand": 3, "conversion": "bin"},
            {"kind": "subtract", "operand": 7, "base": 8},
        ),
        step_count=3,
    )

    result = challenge.evaluate(["12", "110110", "57"])

    assert result.passed is True
    assert [step.expected for step in result.step_results] == ["0x12", "0b110110", "0o57"]
    assert [step.output_base for step in result.step_results] == [16, 2, 8]
    assert [step.label for step in result.step_results] == [
        "Add 5, then convert to hexadecimal",
        "Multiply by 3, then convert to binary",
        "Subtract 7, then convert to octal",
    ]


def test_answer_conversion_updates_login_mission_with_progress() -> None:
    oauth = FakeOAuth()
    oauth.clients["google"] = FakeRemoteApp()
    auth = FunAuth(oauth, prompt_deck=PromptDeck(default_fun_prompts()))
    session: dict[str, object] = {}
    auth.prepare_login(session, "google", prompt_key="conversion-lock")

    result = auth.answer_conversion(session, ["0x12", "0b110110", "0o57"])

    assert result.passed is True
    assert session["_fun_auth_mission"]["challenge_passed"] is True
    assert session["_fun_auth_mission"]["conversion_correct_count"] == 3
    assert session["_fun_auth_mission"]["conversion_step_count"] == 3


def test_conversion_operator_guess_shows_numbers_and_checks_submitted_operators() -> None:
    challenge = build_conversion_operator_guess_challenge(
        start_value=13,
        steps=(
            ConversionStep("add", 5, output_base=16),
            ConversionStep("multiply", 3, output_base=2),
            ConversionStep("subtract", 7, output_base=8),
        ),
    )

    result = challenge.evaluate(["+ hex", "* bin", "- oct"])

    assert result.passed is True
    assert result.display_values == ("13", "0x12", "0b110110", "0o57")
    assert [step.correct for step in result.step_results] == [True, True, True]
    assert [step.expected_move for step in result.step_results] == [
        "+ hexadecimal",
        "* binary",
        "- octal",
    ]


def test_conversion_operator_guess_applies_user_operators_instead_of_fixed_answers() -> None:
    challenge = build_conversion_operator_guess_challenge(
        start_value=13,
        steps=(
            {"kind": "add", "operand": 5, "convert_to": "hex"},
            {"kind": "multiply", "operand": 3, "conversion": "bin"},
        ),
    )

    result = challenge.evaluate(["- hex", "* bin"])

    assert result.passed is False
    assert result.step_results[0].computed_value == 8
    assert result.step_results[0].expected_value == 18
    assert result.step_results[0].correct is False
    assert result.step_results[1].correct is True


def test_answer_conversion_operators_updates_login_mission_with_progress() -> None:
    oauth = FakeOAuth()
    oauth.clients["google"] = FakeRemoteApp()
    auth = FunAuth(oauth, prompt_deck=PromptDeck(default_fun_prompts()))
    session: dict[str, object] = {}
    auth.prepare_login(session, "google", prompt_key="operator-conversion-lock")

    result = auth.answer_conversion_operators(session, ["+ hex", "* bin", "- oct"])

    assert result.passed is True
    assert session["_fun_auth_mission"]["challenge_passed"] is True
    assert session["_fun_auth_mission"]["conversion_operator_correct_count"] == 3
    assert session["_fun_auth_mission"]["conversion_operator_step_count"] == 3


def test_parse_conversion_operator_accepts_only_operator_and_base_forms() -> None:
    assert parse_conversion_operator("+", template_step=ConversionStep("add", 5)).apply(10) == (
        15,
        "Add 5",
        10,
    )
    assert parse_conversion_operator(
        "mul bin",
        template_step=ConversionStep("multiply", 3),
    ).apply(10) == (30, "Multiply by 3, then convert to binary", 2)
    assert parse_conversion_operator("hex").apply(10) == (
        10,
        "Keep the current value, then convert to hexadecimal",
        16,
    )
    with pytest.raises(FunAuthDenied, match="thought it was that easy"):
        parse_conversion_operator("+5", template_step=ConversionStep("add", 5))


def test_conversion_operator_guess_teases_number_shortcuts() -> None:
    challenge = build_conversion_operator_guess_challenge(
        start_value=13,
        steps=(ConversionStep("add", 5, output_base=16),),
    )

    result = challenge.evaluate(["+5"])

    assert result.passed is False
    assert "thought it was that easy" in result.message
    assert result.step_results[0].message == result.message


def test_conversion_helpers_format_supported_bases() -> None:
    assert format_number_for_base(18, 10) == "18"
    assert format_number_for_base(18, 16) == "0x12"
    assert format_number_for_base(18, 2) == "0b10010"
    assert format_number_for_base(18, 8) == "0o22"
    assert format_number_for_base(-18, 16) == "-0x12"


def test_default_conversion_challenge_can_be_shortened() -> None:
    challenge = default_conversion_challenge(step_count=3)

    result = challenge.evaluate(["0x12", "0b110110", "0o57"])

    assert result.passed is True
    assert len(result.step_results) == 3


def test_answer_popup_acknowledges_authorized_message() -> None:
    oauth = FakeOAuth()
    oauth.clients["google"] = FakeRemoteApp()
    auth = FunAuth(oauth, prompt_deck=PromptDeck(default_fun_prompts()))
    session: dict[str, object] = {}
    mission = auth.prepare_login(session, "google", prompt_key="authorized-popup")

    result = auth.answer_popup(session)

    assert mission.prompt.popup is not None
    assert mission.prompt.popup.message == "I'm authorized"
    assert result.passed is True
    assert result.answer_key == "authorized-popup"
    assert session["_fun_auth_mission"]["challenge_passed"] is True


def test_default_popup_message_can_be_customized_from_input() -> None:
    prompts = default_fun_prompts(popup_message="I am not stupid")
    popup_prompt = next(prompt for prompt in prompts if prompt.key == "authorized-popup")

    assert popup_prompt.prompt == "I am not stupid"
    assert popup_prompt.popup is not None
    assert popup_prompt.popup.message == "I am not stupid"


def test_fun_auth_uses_custom_popup_message_for_default_deck() -> None:
    oauth = FakeOAuth()
    auth = FunAuth(oauth, popup_message="I am not stupid")
    session: dict[str, object] = {}

    mission = auth.prepare_login(session, "google", prompt_key="authorized-popup")

    assert mission.prompt.prompt == "I am not stupid"
    assert mission.prompt.popup is not None
    assert mission.prompt.popup.message == "I am not stupid"


def test_answer_popup_can_be_dismissed() -> None:
    oauth = FakeOAuth()
    auth = FunAuth(oauth, prompt_deck=PromptDeck(default_fun_prompts()))
    session: dict[str, object] = {}
    auth.prepare_login(session, "google", prompt_key="authorized-popup")

    result = auth.answer_popup(session, accepted=False)

    assert result.passed is False
    assert result.answer_key == "dismissed"
    assert session["_fun_auth_mission"]["challenge_passed"] is False


def test_number_guess_passes_when_guess_matches_session_target() -> None:
    oauth = FakeOAuth()
    state_store = InMemoryFunStateStore()
    auth = FunAuth(oauth, prompt_deck=PromptDeck(default_fun_prompts()), state_store=state_store)
    session: dict[str, object] = {}
    auth.prepare_login(session, "google", prompt_key="number-guess")
    mission_id = session["_fun_auth_mission"]["mission_id"]
    state_store.save_number_guess(
        str(mission_id),
        "number-guess",
        NumberGuessMissionState(target=7),
    )

    result = auth.answer_number_guess(session, 7)

    assert result.passed is True
    assert result.hint == "correct"
    assert session["_fun_auth_mission"]["challenge_passed"] is True
    assert "number-guess_target" not in session["_fun_auth_mission"]
    assert state_store.get_number_guess(str(mission_id), "number-guess") == NumberGuessMissionState(
        target=7,
        attempts_used=1,
    )


def test_number_guess_gives_directional_feedback_before_reset() -> None:
    oauth = FakeOAuth()
    challenge = NumberGuessChallenge(key="guess-small", range_min=1, range_max=5, max_tries=3)
    prompt = FunPrompt(
        key="guess-small",
        title="Guess Small",
        prompt="Guess.",
        options=(),
        success_message=challenge.success_message,
        failure_message=challenge.failure_message,
        number_guess=challenge,
    )
    state_store = InMemoryFunStateStore()
    auth = FunAuth(oauth, prompt_deck=PromptDeck((prompt,)), state_store=state_store)
    session: dict[str, object] = {}
    auth.prepare_login(session, "google", prompt_key="guess-small")
    mission_id = session["_fun_auth_mission"]["mission_id"]
    state_store.save_number_guess(
        str(mission_id),
        "guess-small",
        NumberGuessMissionState(target=4),
    )

    result = auth.answer_number_guess(session, 2)

    assert result.passed is False
    assert result.reset is False
    assert result.hint == "too-low"
    assert result.attempts_remaining == 2
    assert "too low" in result.message
    assert state_store.get_number_guess(str(mission_id), "guess-small") == NumberGuessMissionState(
        target=4,
        attempts_used=1,
    )


def test_number_guess_resets_and_blames_user_after_failed_tries() -> None:
    oauth = FakeOAuth()
    challenge = NumberGuessChallenge(key="guess-tiny", range_min=1, range_max=2, max_tries=2)
    prompt = FunPrompt(
        key="guess-tiny",
        title="Guess Tiny",
        prompt="Guess.",
        options=(),
        success_message=challenge.success_message,
        failure_message=challenge.failure_message,
        number_guess=challenge,
    )
    state_store = InMemoryFunStateStore()
    auth = FunAuth(oauth, prompt_deck=PromptDeck((prompt,)), state_store=state_store)
    session: dict[str, object] = {}
    auth.prepare_login(session, "google", prompt_key="guess-tiny")
    mission_id = session["_fun_auth_mission"]["mission_id"]
    state_store.save_number_guess(
        str(mission_id),
        "guess-tiny",
        NumberGuessMissionState(target=2),
    )

    first = auth.answer_number_guess(session, 1)
    second = auth.answer_number_guess(session, 1)

    assert first.reset is False
    assert second.reset is True
    assert second.passed is False
    assert "your failure" in second.message
    assert session["_fun_auth_mission"]["challenge_passed"] is False
    reset_state = state_store.get_number_guess(str(mission_id), "guess-tiny")
    assert reset_state is not None
    assert reset_state.attempts_used == 0
    assert 1 <= reset_state.target <= 2


def test_number_guess_rejects_guess_outside_range() -> None:
    oauth = FakeOAuth()
    auth = FunAuth(oauth, prompt_deck=PromptDeck(default_fun_prompts()))
    session: dict[str, object] = {}
    auth.prepare_login(session, "google", prompt_key="number-guess")

    with pytest.raises(FunAuthDenied, match="outside"):
        auth.answer_number_guess(session, 99)


def test_complete_login_extracts_identity_and_clears_mission_state() -> None:
    oauth = FakeOAuth()
    oauth.clients["google"] = FakeRemoteApp(
        token={
            "access_token": "provider-token",
            "userinfo": {
                "sub": "user-123",
                "name": "Alice Example",
                "email": "alice@example.com",
                "picture": "https://example.com/alice.png",
            },
        }
    )
    auth = FunAuth(
        oauth,
        prompt_deck=PromptDeck(default_fun_prompts()),
        trusted_email_domains=["example.com"],
    )
    session: dict[str, object] = {}
    auth.prepare_login(session, "google", next_url="/dashboard", prompt_key="authorized-popup")
    auth.answer_popup(session)

    result = auth.complete_login(session, code="callback-code")

    assert result.identity.provider_name == "google"
    assert result.identity.subject == "user-123"
    assert result.identity.display_name == "Alice Example"
    assert result.identity.email == "alice@example.com"
    assert result.identity.avatar_url == "https://example.com/alice.png"
    assert result.identity.to_session()["display_name"] == "Alice Example"
    assert result.token["access_token"] == "provider-token"
    assert result.next_url == "/dashboard"
    assert result.prompt_key == "authorized-popup"
    assert result.welcome.headline == "Welcome back, Alice Example"
    assert session == {}
    assert oauth.clients["google"].token_kwargs == {"code": "callback-code"}


def test_complete_login_requires_finished_prompt() -> None:
    oauth = FakeOAuth()
    oauth.clients["google"] = FakeRemoteApp(
        token={
            "userinfo": {
                "sub": "user-123",
                "email": "alice@example.com",
            },
        }
    )
    auth = FunAuth(oauth, prompt_deck=PromptDeck(default_fun_prompts()))
    session: dict[str, object] = {}
    auth.prepare_login(session, "google", prompt_key="authorized-popup")

    with pytest.raises(FunAuthDenied, match="Finish the login prompt"):
        auth.complete_login(session)


def test_complete_login_rejects_untrusted_email_domain() -> None:
    oauth = FakeOAuth()
    oauth.clients["google"] = FakeRemoteApp(
        token={
            "userinfo": {
                "sub": "user-123",
                "name": "Alice Example",
                "email": "alice@outside.example",
            }
        }
    )
    auth = FunAuth(oauth, trusted_email_domains=["example.com"])
    session: dict[str, object] = {}
    auth.prepare_login(session, "google", prompt_key="authorized-popup")
    auth.answer_popup(session)

    with pytest.raises(FunAuthDenied, match="email domain"):
        auth.complete_login(session)


def test_provider_helpers_create_google_and_microsoft_metadata_urls() -> None:
    google = google_provider("google-client", "google-secret")
    microsoft = microsoft_entra_provider(
        "microsoft-client",
        "microsoft-secret",
        tenant_id="organizations",
    )

    assert google.name == "google"
    assert google.server_metadata_url == (
        "https://accounts.google.com/.well-known/openid-configuration"
    )
    assert microsoft.name == "microsoft"
    assert microsoft.server_metadata_url == (
        "https://login.microsoftonline.com/organizations/v2.0/.well-known/openid-configuration"
    )


def test_fun_auth_ideas_separate_cosmetic_and_real_security_layers() -> None:
    ideas = default_fun_auth_ideas()

    assert {idea.key for idea in ideas} >= {
        "sso-badge-ceremony",
        "drawing-template-gate",
        "conversion-lock",
        "authorized-popup",
        "number-guess",
        "passkey-step-up",
    }
    assert any("OIDC" in idea.security_role for idea in ideas)


def test_cli_lists_prompts_as_json(capsys: pytest.CaptureFixture[str]) -> None:
    cli_main(["prompts", "--json"])

    output = capsys.readouterr().out
    assert "authorized-popup" in output
    assert "draw-key" in output


def test_render_prompt_card_outputs_polished_prompt_markup() -> None:
    auth = FunAuth()
    session: dict[str, object] = {}
    mission = auth.prepare_mission(
        session,
        prompt_keys=("authorized-popup", "operator-conversion-lock"),
    )

    html = render_prompt_card(mission, action="/login/popup")

    assert 'class="funthenticate-card"' in html
    assert "Step 1 of 2" in html
    assert "Authorization Popup" in html
    assert 'action="/login/popup"' in html
