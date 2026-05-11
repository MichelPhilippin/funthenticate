from __future__ import annotations

import html
import json
from types import SimpleNamespace

import pytest

from funthenticate import (
    FunAuth,
    FunAuthDenied,
    default_key_drawing_template,
    default_stylesheet,
    parse_drawing_strokes,
    render_prompt_card,
)

flask = pytest.importorskip("flask")
Flask = flask.Flask
Response = flask.Response
jsonify = flask.jsonify
redirect = flask.redirect
request = flask.request
session = flask.session
url_for = flask.url_for


def create_showcase_demo_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = "showcase-demo-secret"
    auth = FunAuth()

    @app.get("/")
    def index() -> str:
        return '<a href="/login">Try Funthenticate</a>'

    @app.get("/funthenticate.css")
    def funthenticate_css() -> Response:
        return Response(default_stylesheet(), mimetype="text/css")

    @app.get("/login")
    def login() -> str:
        mission = auth.prepare_mission(
            session,
            next_url="/dashboard",
            prompt_keys=(
                "authorized-popup",
                "draw-key",
                "conversion-lock",
                "operator-conversion-lock",
            ),
        )
        return _page(_render_showcase_card(mission))

    @app.post("/login/answer")
    def answer_login() -> str | Response:
        try:
            result = _answer_current_prompt(auth)
        except (FunAuthDenied, ValueError) as error:
            mission = auth.current_mission(session)
            return _page(_render_showcase_card(mission), str(error)), 400

        if not result.passed:
            mission = auth.current_mission(session)
            return _page(
                _render_showcase_card(mission),
                result.message,
                _result_feedback(result),
            ), 400

        try:
            done = auth.complete_fun(session)
        except FunAuthDenied:
            mission = auth.current_mission(session)
            return _page(_render_showcase_card(mission))

        session["funthenticated"] = True
        session["welcome_badge"] = done.welcome.badge
        return redirect(done.next_url or url_for("dashboard"))

    @app.post("/login/check")
    def check_login_step() -> Response:
        try:
            index = int(request.form["index"])
            step = _check_current_prompt_step(auth, index)
        except (FunAuthDenied, ValueError) as error:
            return jsonify({"correct": False, "message": str(error)}), 400
        return jsonify(
            {
                "correct": step["correct"],
                "message": step["message"],
            }
        )

    @app.get("/dashboard")
    def dashboard() -> str | tuple[str, int]:
        if not session.get("funthenticated"):
            return "Fun gate required.", 403
        return f"Dashboard unlocked: {session['welcome_badge']}"

    return app


def _render_showcase_card(mission: object) -> str:
    card_html = render_prompt_card(
        mission,
        action=url_for("answer_login"),
        check_action=url_for("check_login_step"),
    )
    prompt = mission.prompt
    if prompt.operator_guess_challenge is not None:
        card_html = _remove_operator_value_chain(card_html, prompt.operator_guess_challenge)
        card_html = _add_operator_fields(card_html, prompt.operator_guess_challenge)
        return _add_operator_examples_before_submit(card_html)
    if prompt.conversion_challenge is not None:
        return card_html.replace("</form>", f"{_conversion_examples()}</form>")
    return card_html


def _answer_current_prompt(auth: FunAuth) -> object:
    prompt = auth.current_mission(session).prompt
    if prompt.popup is not None:
        return auth.answer_popup(session, accepted=request.form.get("accepted") == "true")
    if prompt.operator_guess_challenge is not None:
        result = _evaluate_showcase_operator_answers(
            prompt.operator_guess_challenge,
            request.form.getlist("operators"),
        )
        if result.passed:
            return auth.answer_conversion_operators(
                session,
                [_core_operator_move(step) for step in prompt.operator_guess_challenge.steps],
            )
        return result
    if prompt.conversion_challenge is not None:
        return auth.answer_conversion(session, request.form.getlist("answers"))
    if prompt.number_guess is not None:
        return auth.answer_number_guess(session, int(request.form["guess"]))
    if prompt.drawing_template is not None:
        return auth.answer_drawing(session, parse_drawing_strokes(request.form["strokes"]))
    return auth.answer_prompt(session, request.form["answer_key"])


def _check_current_prompt_step(auth: FunAuth, index: int) -> dict[str, object]:
    if index < 0:
        raise FunAuthDenied("Step index is invalid.")
    prompt = auth.current_mission(session).prompt
    if prompt.operator_guess_challenge is not None:
        result = _evaluate_showcase_operator_answers(
            prompt.operator_guess_challenge,
            request.form.getlist("operators"),
        )
        return _field_check_step(result.step_results, index)
    if prompt.conversion_challenge is not None:
        result = prompt.conversion_challenge.evaluate(request.form.getlist("answers"))
        return _field_check_step(result.step_results, index)
    raise FunAuthDenied("This prompt does not have live step feedback.")


def _page(card_html: str, message: str = "", feedback_html: str = "") -> str:
    message_html = f'<p class="demo-message">{html.escape(message)}</p>' if message else ""
    return f"""
    <!doctype html>
    <html lang="en">
      <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <link rel="stylesheet" href="/funthenticate.css">
        <style>
          .demo-message,
          .demo-examples,
          .demo-feedback {{
            width: min(100% - 32px, 520px);
            margin: 16px auto;
            color: #172026;
            font-family: Inter, ui-sans-serif, system-ui, sans-serif;
          }}

          .demo-examples,
          .demo-feedback {{
            padding: 12px;
            border: 1px solid #d5e1e8;
            border-radius: 8px;
            background: #fbfcfb;
          }}

          .demo-examples {{
            padding: 0;
            border: 0;
            background: transparent;
          }}

          .demo-examples p,
          .demo-feedback p {{
            margin: 0 0 10px;
            color: #596873;
            font-size: 0.9rem;
          }}

          .demo-example-grid {{
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
          }}

          .demo-example-grid code {{
            padding: 5px 8px;
            border-radius: 6px;
            background: rgba(0, 120, 212, 0.14);
            font-size: 0.9rem;
            font-weight: 400;
          }}

          .demo-example-prefix {{
            font-weight: 700;
          }}

          .demo-field-example {{
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
            align-items: center;
            margin-top: 2px;
            font-weight: 700;
          }}

          .demo-field-example code {{
            padding: 3px 6px;
            border-radius: 6px;
            background: rgba(0, 120, 212, 0.14);
            color: #172026;
            font-weight: 400;
          }}

          .demo-field-question {{
            color: #172026;
            font-size: 0.95rem;
            font-weight: 700;
          }}

          .demo-feedback ol {{
            display: grid;
            gap: 8px;
            margin: 0;
            padding-left: 22px;
          }}

          .demo-feedback li {{
            line-height: 1.45;
          }}

          .demo-feedback strong {{
            color: #2a955d;
          }}

          .demo-feedback .demo-miss {{
            color: #df1a36;
          }}

        </style>
        <title>Funthenticate Showcase Demo</title>
      </head>
      <body>
        {message_html}
        {feedback_html}
        {card_html}
      </body>
    </html>
    """


def _result_feedback(result: object) -> str:
    step_results = getattr(result, "step_results", ())
    if not step_results:
        return ""
    rows = "\n".join(_feedback_row(step_result) for step_result in step_results)
    return f"""
      <section class="demo-feedback" aria-label="Step feedback">
        <p>Step feedback</p>
        <ol>{rows}</ol>
      </section>
    """


def _feedback_row(step_result: object) -> str:
    correct = bool(getattr(step_result, "correct", False))
    status_class = "" if correct else ' class="demo-miss"'
    status = "correct" if correct else "needs another try"
    submitted = _submitted_value(step_result)
    detail = _step_detail(step_result)
    return (
        "<li>"
        f"<strong{status_class}>{html.escape(status)}</strong>"
        f" {detail}"
        f" <span>Submitted: <code>{html.escape(submitted)}</code></span>"
        "</li>"
    )


def _feedback_step(step_result: object) -> dict[str, object]:
    return {
        "correct": bool(getattr(step_result, "correct", False)),
        "detail": _step_detail_text(step_result),
        "submitted": _submitted_value(step_result),
    }


def _field_check_step(step_results: tuple[object, ...], index: int) -> dict[str, object]:
    if index >= len(step_results):
        raise FunAuthDenied("Step index is invalid.")
    step_result = step_results[index]
    correct = bool(getattr(step_result, "correct", False))
    return {
        "correct": correct,
        "message": "Correct." if correct else "That box needs another try.",
    }


def _evaluate_showcase_operator_answers(challenge: object, operators: list[str]) -> object:
    value = challenge.start_value
    display_values = challenge.display_values()
    step_results = []
    for index, step in enumerate(challenge.steps, start=1):
        expected_value, _label, expected_base = step.apply(value)
        submitted = operators[index - 1] if index <= len(operators) else ""
        try:
            candidate_results = tuple(
                _evaluate_showcase_operator_candidate(
                    value,
                    step,
                    expected_value,
                    expected_base,
                    candidate,
                )
                for candidate in _split_showcase_entries(submitted)
            )
            correct_result = next(
                (result for result in candidate_results if result.correct),
                None,
            )
            best_result = correct_result or candidate_results[0]
            computed_value = best_result.computed_value
            correct = best_result.correct
            message = None
        except FunAuthDenied as error:
            computed_value = None
            correct = False
            message = str(error)

        step_results.append(
            SimpleNamespace(
                index=index,
                displayed_from=display_values[index - 1],
                displayed_to=display_values[index],
                submitted_move=submitted,
                expected_move=_expected_showcase_move(step),
                correct=correct,
                computed_value=computed_value,
                expected_value=expected_value,
                message=message,
            )
        )
        value = expected_value

    passed = len(operators) == len(challenge.steps) and all(
        step_result.correct for step_result in step_results
    )
    first_error = next(
        (step_result.message for step_result in step_results if step_result.message),
        None,
    )
    return SimpleNamespace(
        passed=passed,
        step_results=tuple(step_results),
        display_values=display_values,
        message=challenge.success_message if passed else first_error or challenge.failure_message,
    )


def _evaluate_showcase_operator_candidate(
    value: int,
    step: object,
    expected_value: int,
    expected_base: int,
    submitted: str,
) -> object:
    parsed = _parse_showcase_operator_answer(submitted, step.operand)
    operator = step.kind if parsed.operator == "default" else parsed.operator
    output_base = expected_base if parsed.output_base is None else parsed.output_base
    computed_value = _apply_showcase_operator(value, operator, parsed.operand)
    return SimpleNamespace(
        computed_value=computed_value,
        correct=computed_value == expected_value and output_base == expected_base,
    )


def _split_showcase_entries(value: str) -> tuple[str, ...]:
    entries = tuple(entry.strip() for entry in value.replace(";", ",").split(",") if entry.strip())
    if not entries:
        raise FunAuthDenied("Enter the missing number.")
    return entries


def _parse_showcase_operator_answer(value: str, default_operand: int | None) -> object:
    _reject_word_operator_aliases([value])
    compact = "".join(value.lower().split())
    if not compact:
        raise FunAuthDenied("Enter the missing number.")

    operator = None
    for candidate in ("//", "**", "+", "-", "*", "%"):
        if compact.startswith(candidate):
            operator = candidate
            compact = compact.removeprefix(candidate)
            break
    if operator is None:
        operator = "default"

    base_token = None
    for candidate in ("hex", "bin", "oct", "dec"):
        if candidate in compact:
            if base_token is not None:
                raise FunAuthDenied("Use only one conversion per box.")
            base_token = candidate
            compact = compact.replace(candidate, "", 1)

    if base_token is None:
        base_token = "default"

    operand = default_operand
    if compact:
        operand = _parse_showcase_number(compact)
    if operand is None:
        raise FunAuthDenied("Enter the missing number.")

    return SimpleNamespace(
        operator=operator,
        operand=operand,
        output_base=None
        if base_token == "default"
        else {"bin": 2, "oct": 8, "dec": 10, "hex": 16}[base_token],
    )


def _parse_showcase_number(value: str) -> int:
    try:
        return int(value, 0)
    except ValueError as error:
        raise FunAuthDenied("Numbers must be decimal, 0x hex, 0b binary, or 0o octal.") from error


def _apply_showcase_operator(value: int, operator: str, operand: int | None) -> int:
    if operator in {"keep", "identity"}:
        return value
    if operand is None:
        raise FunAuthDenied("Add a number for this operator.")
    if operator in {"+", "add"}:
        return value + operand
    if operator in {"-", "subtract"}:
        return value - operand
    if operator in {"*", "multiply"}:
        return value * operand
    if operator in {"//", "integer_divide"}:
        if operand == 0:
            raise FunAuthDenied("Division by zero is not allowed.")
        return value // operand
    if operator in {"%", "modulo"}:
        if operand == 0:
            raise FunAuthDenied("Modulo by zero is not allowed.")
        return value % operand
    if operator in {"**", "power"}:
        return value**operand
    raise FunAuthDenied("Use a supported symbol operator.")


def _expected_showcase_move(step: object) -> str:
    conversion = {2: "bin", 8: "oct", 10: "dec", 16: "hex"}[step.resolved_output_base()]
    operator = {
        "add": "+",
        "subtract": "-",
        "multiply": "*",
        "integer_divide": "//",
        "modulo": "%",
        "power": "**",
        "identity": "",
    }.get(step.kind, step.kind)
    operand = "" if step.operand is None else str(step.operand)
    return f"{operator}{conversion}{operand}"


def _core_operator_move(step: object) -> str:
    conversion = {2: "bin", 8: "oct", 10: "dec", 16: "hex"}[step.resolved_output_base()]
    operator = {
        "add": "+",
        "subtract": "-",
        "multiply": "*",
        "integer_divide": "//",
        "modulo": "%",
        "power": "**",
        "identity": "",
    }.get(step.kind, step.kind)
    return f"{operator} {conversion}".strip()


def _remove_operator_value_chain(card_html: str, challenge: object) -> str:
    values = challenge.display_values()
    original = html.escape("  ->  ".join(values), quote=True)
    return card_html.replace(
        f'      <div class="funthenticate-value-chain">{original}</div>\n',
        "",
    )


def _step_symbol(step: object) -> str:
    return {
        "add": "+",
        "subtract": "-",
        "multiply": "*",
        "integer_divide": "//",
        "modulo": "%",
        "power": "**",
        "identity": "",
    }.get(step.kind, step.kind)


def _add_operator_fields(card_html: str, challenge: object) -> str:
    values = challenge.display_values()
    for index, step in enumerate(challenge.steps, start=1):
        card_html = card_html.replace(
            (f'<span>Move {index}</span><input name="operators" autocomplete="off" required>'),
            _operator_field_markup(index, step, values),
            1,
        )
    return card_html


def _add_operator_examples_before_submit(card_html: str) -> str:
    submit_button = '      <button class="funthenticate-primary" type="submit">Connect</button>'
    return card_html.replace(submit_button, f"{_operator_examples()}\n{submit_button}", 1)


def _operator_field_markup(index: int, step: object, values: tuple[str, ...]) -> str:
    return (
        f'<span class="demo-field-question">{_operator_question(index, step, values)}</span>'
        '<input name="operators" autocomplete="off" required>'
    )


def _operator_question(index: int, step: object, values: tuple[str, ...]) -> str:
    return (
        f"{html.escape(values[index - 1])} {_step_symbol(step)} ___ = {html.escape(values[index])}"
    )


def _normalize_operator_answers(operators: list[str]) -> list[str]:
    return [_normalize_operator_answer(operator) for operator in operators]


def _normalize_operator_answer(operator: str) -> str:
    compact = "".join(operator.split())
    if compact[:1] in {"+", "-", "*", "%"} and len(compact) > 1:
        return f"{compact[0]} {compact[1:]}"
    if compact.startswith("//") and len(compact) > 2:
        return f"// {compact[2:]}"
    if compact.startswith("**") and len(compact) > 2:
        return f"** {compact[2:]}"
    return compact


def _submitted_value(step_result: object) -> str:
    submitted = getattr(step_result, "submitted_move", None)
    if submitted is None:
        submitted = getattr(step_result, "submitted", None)
    return "blank" if submitted is None else str(submitted)


def _step_detail(step_result: object) -> str:
    return html.escape(_step_detail_text(step_result))


def _step_detail_text(step_result: object) -> str:
    displayed_from = getattr(step_result, "displayed_from", None)
    displayed_to = getattr(step_result, "displayed_to", None)
    if displayed_from is not None and displayed_to is not None:
        return f"for {displayed_from} -> {displayed_to}."
    label = getattr(step_result, "label", None)
    if label is not None:
        return f"for {label}."
    return "."


def _reject_word_operator_aliases(operators: list[str]) -> None:
    word_operator_aliases = {
        "add",
        "sub",
        "subtract",
        "mul",
        "multiply",
        "div",
        "divide",
        "mod",
        "modulo",
        "pow",
        "power",
        "keep",
        "identity",
        "noop",
    }
    for operator in operators:
        compact = "".join(operator.lower().split())
        if any(compact == alias or compact.startswith(alias) for alias in word_operator_aliases):
            raise FunAuthDenied("Use symbol operators like +, -, and * with conversions.")


def _operator_examples() -> str:
    return """
      <div class="demo-examples" aria-label="Accepted number example">
        <p>
          <span class="demo-example-prefix">Example entry:</span>
          <code>5</code>, <code>0x05</code>, <code>0b101</code>, or <code>0o5</code>
        </p>
      </div>
    """


def _conversion_examples() -> str:
    return """
      <div class="demo-examples" aria-label="Accepted answer examples">
        <p>Answers can use prefixes or plain digits for the requested base. Examples:</p>
        <div class="demo-example-grid">
          <code>0x12</code>
          <code>0b110110</code>
          <code>0o57</code>
          <code>12</code>
          <code>110110</code>
          <code>57</code>
        </div>
      </div>
    """


def _default_key_strokes_payload() -> str:
    return json.dumps(
        [
            [[point.x, point.y] for point in stroke]
            for stroke in default_key_drawing_template().strokes
        ]
    )


def _default_conversion_answers() -> list[str]:
    return ["0x12", "0b110110", "0o57"]


def _advance_to_conversion_lock(client: object) -> None:
    client.get("/login")
    client.post("/login/answer", data={"accepted": "true"})
    client.post("/login/answer", data={"strokes": _default_key_strokes_payload()})


def _advance_to_operator_lock(client: object) -> None:
    _advance_to_conversion_lock(client)
    client.post("/login/answer", data={"answers": _default_conversion_answers()})


def test_showcase_flask_server_completes_fun_only_flow() -> None:
    app = create_showcase_demo_app()
    client = app.test_client()

    login_response = client.get("/login")
    popup_response = client.post("/login/answer", data={"accepted": "true"})
    drawing_response = client.post(
        "/login/answer",
        data={"strokes": _default_key_strokes_payload()},
    )
    conversion_response = client.post(
        "/login/answer",
        data={"answers": _default_conversion_answers()},
    )
    operator_response = client.post(
        "/login/answer",
        data={"operators": ["0x05", "0b11", "0o7"]},
        follow_redirects=True,
    )

    assert login_response.status_code == 200
    assert b"Authorization Popup" in login_response.data
    assert popup_response.status_code == 200
    assert b"Draw the Key" in popup_response.data
    assert b"Draw the key shape to unlock this login." in popup_response.data
    assert b'name="strokes"' in popup_response.data
    assert b"funthenticate-canvas" in popup_response.data
    assert drawing_response.status_code == 200
    assert b"Conversion Lock" in drawing_response.data
    assert b'name="answers"' in drawing_response.data
    assert b"Answers can use prefixes or plain digits" in drawing_response.data
    assert conversion_response.status_code == 200
    assert b"Operator Conversion Lock" in conversion_response.data
    assert b'demo-field-question">13 + ___ = 0x12' in conversion_response.data
    assert b'demo-field-question">0x12 * ___ = 0b110110' in conversion_response.data
    assert b"funthenticate-value-chain" not in conversion_response.data
    assert b"-&gt;" not in conversion_response.data
    assert b"0x05" in conversion_response.data
    assert b"0b101" in conversion_response.data
    assert b"0x03" not in conversion_response.data
    assert b"0x07" not in conversion_response.data
    assert conversion_response.data.count(b"Example entry") == 1
    assert b'demo-example-prefix">Example entry:' in conversion_response.data
    assert conversion_response.data.index(b"Example entry") < conversion_response.data.index(
        b"Connect"
    )
    assert b"Move" not in conversion_response.data
    assert b"Accepted move examples" not in conversion_response.data
    assert b"add hex" not in conversion_response.data
    assert b"mul bin" not in conversion_response.data
    assert operator_response.status_code == 200
    assert b"Dashboard unlocked: Certified Fun" in operator_response.data


def test_showcase_flask_server_keeps_user_on_drawing_prompt_after_wrong_key() -> None:
    app = create_showcase_demo_app()
    client = app.test_client()

    client.get("/login")
    client.post("/login/answer", data={"accepted": "true"})
    response = client.post("/login/answer", data={"strokes": "[[[0, 0], [5, 10], [10, 0]]]"})

    assert response.status_code == 400
    assert b"That drawing did not match the template closely enough." in response.data
    assert b"Draw the Key" in response.data
    assert b"funthenticate-canvas" in response.data


def test_showcase_flask_server_keeps_user_on_prompt_after_wrong_operator() -> None:
    app = create_showcase_demo_app()
    client = app.test_client()

    _advance_to_operator_lock(client)
    response = client.post("/login/answer", data={"operators": ["- hex", "* bin", "- oct"]})

    assert response.status_code == 400
    assert b"Some operators do not connect the shown numbers." in response.data
    assert b"Operator Conversion Lock" in response.data
    assert b"Step feedback" in response.data
    assert b"13 -&gt; 0x12" in response.data
    assert b"needs another try" in response.data
    assert b"0x12 -&gt; 0b110110" in response.data
    assert b"correct" in response.data


def test_showcase_flask_server_checks_current_conversion_box_without_advancing() -> None:
    app = create_showcase_demo_app()
    client = app.test_client()

    _advance_to_conversion_lock(client)
    response = client.post(
        "/login/check",
        data={"answers": ["0x12", "", ""], "index": "0"},
    )
    dashboard_response = client.get("/dashboard")

    assert response.status_code == 200
    assert response.json == {"correct": True, "message": "Correct."}
    assert dashboard_response.status_code == 403


def test_showcase_flask_server_rejects_only_current_wrong_conversion_box() -> None:
    app = create_showcase_demo_app()
    client = app.test_client()

    _advance_to_conversion_lock(client)
    response = client.post(
        "/login/check",
        data={"answers": ["0x11", "", ""], "index": "0"},
    )

    assert response.status_code == 200
    assert response.json == {"correct": False, "message": "That box needs another try."}


def test_showcase_flask_server_checks_current_operator_box_without_advancing() -> None:
    app = create_showcase_demo_app()
    client = app.test_client()

    _advance_to_operator_lock(client)
    response = client.post(
        "/login/check",
        data={"operators": ["0x05", "", ""], "index": "0"},
    )
    dashboard_response = client.get("/dashboard")

    assert response.status_code == 200
    assert response.json == {"correct": True, "message": "Correct."}
    assert dashboard_response.status_code == 403


def test_showcase_flask_server_allows_multiple_entries_for_current_question() -> None:
    app = create_showcase_demo_app()
    client = app.test_client()

    _advance_to_operator_lock(client)
    response = client.post(
        "/login/check",
        data={"operators": ["4, 0x05, 0b110", "", ""], "index": "0"},
    )

    assert response.status_code == 200
    assert response.json == {"correct": True, "message": "Correct."}


@pytest.mark.parametrize("operand", ["5", "0x05", "0b101", "0o5"])
def test_showcase_flask_server_accepts_decimal_hex_binary_and_octal_operands(
    operand: str,
) -> None:
    app = create_showcase_demo_app()
    client = app.test_client()

    _advance_to_operator_lock(client)
    response = client.post(
        "/login/check",
        data={"operators": [f"+ hex {operand}", "", ""], "index": "0"},
    )

    assert response.status_code == 200
    assert response.json == {"correct": True, "message": "Correct."}


def test_showcase_flask_server_rejects_only_current_wrong_operator_box() -> None:
    app = create_showcase_demo_app()
    client = app.test_client()

    _advance_to_operator_lock(client)
    response = client.post(
        "/login/check",
        data={"operators": ["0x04", "", ""], "index": "0"},
    )

    assert response.status_code == 200
    assert response.json == {"correct": False, "message": "That box needs another try."}


def test_showcase_flask_server_rejects_word_operator_aliases() -> None:
    app = create_showcase_demo_app()
    client = app.test_client()

    _advance_to_operator_lock(client)
    response = client.post("/login/answer", data={"operators": ["addhex", "mul bin", "- oct"]})

    assert response.status_code == 400
    assert b"Use symbol operators like +, -, and * with conversions." in response.data
    assert b"Operator Conversion Lock" in response.data


if __name__ == "__main__":
    create_showcase_demo_app().run(debug=True, port=5050)
