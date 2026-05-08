from __future__ import annotations

import html
from importlib import resources

from .core import FunMission, FunPrompt

DEFAULT_FORM_ACTION = ""
DEFAULT_FORM_METHOD = "post"


def default_stylesheet() -> str:
    return (
        resources.files("funthenticate.assets")
        .joinpath("funthenticate.css")
        .read_text(encoding="utf-8")
    )


def render_prompt_card(
    mission: FunMission,
    *,
    action: str = DEFAULT_FORM_ACTION,
    method: str = DEFAULT_FORM_METHOD,
) -> str:
    prompt = mission.prompt
    progress = _progress_text(mission.prompt_index, mission.prompt_count)
    body = _prompt_body(prompt)
    form_open = (
        f'    <form class="funthenticate-form" action="{_escape(action)}" '
        f'method="{_escape(method)}">'
    )
    return "\n".join(
        (
            '<section class="funthenticate-shell">',
            '  <div class="funthenticate-card">',
            '    <header class="funthenticate-header">',
            f'      <p class="funthenticate-progress">{progress}</p>',
            f"      <h1>{_escape(prompt.title)}</h1>",
            _prompt_text(prompt),
            "    </header>",
            form_open,
            body,
            "    </form>",
            "  </div>",
            "</section>",
        )
    )


def _prompt_body(prompt: FunPrompt) -> str:
    if prompt.popup is not None:
        confirm_button = (
            '      <button class="funthenticate-primary funthenticate-popup-submit" '
            f'type="submit">{_escape(prompt.popup.message)}</button>'
        )
        return "\n".join(
            (
                '      <input type="hidden" name="accepted" value="true">',
                confirm_button,
            )
        )
    if prompt.number_guess is not None:
        return "\n".join(
            (
                '      <label class="funthenticate-field">',
                "        <span>Guess</span>",
                '        <input name="guess" type="number" inputmode="numeric" required>',
                "      </label>",
                '      <button class="funthenticate-primary" type="submit">Try</button>',
            )
        )
    if prompt.conversion_challenge is not None:
        fields = [
            _input_field("Step", index, "answers")
            for index, _step in enumerate(prompt.conversion_challenge.steps, start=1)
        ]
        fields.append('      <button class="funthenticate-primary" type="submit">Open</button>')
        return "\n".join(fields)
    if prompt.operator_guess_challenge is not None:
        values = prompt.operator_guess_challenge.display_values()
        fields = [
            f'      <div class="funthenticate-value-chain">{_escape("  ->  ".join(values))}</div>'
        ]
        fields.extend(
            _input_field("Move", index, "operators")
            for index, _step in enumerate(prompt.operator_guess_challenge.steps, start=1)
        )
        fields.append('      <button class="funthenticate-primary" type="submit">Connect</button>')
        return "\n".join(fields)
    if prompt.drawing_template is not None:
        return "\n".join(
            (
                '      <div class="funthenticate-canvas-placeholder" '
                'role="img" aria-label="Drawing pad"></div>',
                '      <button class="funthenticate-primary" type="submit">Match</button>',
            )
        )
    if prompt.options:
        options = [_option_button(option.key, option.label) for option in prompt.options]
        return "\n".join(options)
    return '      <button class="funthenticate-primary" type="submit">Continue</button>'


def _prompt_text(prompt: FunPrompt) -> str:
    if prompt.popup is not None:
        return '      <p class="funthenticate-prompt"></p>'
    return f'      <p class="funthenticate-prompt">{_escape(prompt.prompt)}</p>'


def _progress_text(index: int, count: int) -> str:
    if count <= 1:
        return "Fun gate"
    return f"Step {index + 1} of {count}"


def _input_field(label: str, index: int, name: str) -> str:
    return (
        '      <label class="funthenticate-field">'
        f"<span>{label} {index}</span>"
        f'<input name="{_escape(name)}" autocomplete="off" required>'
        "</label>"
    )


def _option_button(key: str, label: str) -> str:
    return (
        '      <button class="funthenticate-option" type="submit" '
        f'name="answer_key" value="{_escape(key)}">{_escape(label)}</button>'
    )


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)
