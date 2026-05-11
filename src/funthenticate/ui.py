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
    check_action: str | None = None,
) -> str:
    prompt = mission.prompt
    progress = _progress_text(mission.prompt_index, mission.prompt_count)
    body = _prompt_body(prompt, check_action=check_action)
    check_action_attr = (
        f' data-funthenticate-check-action="{_escape(check_action)}"'
        if check_action is not None
        else ""
    )
    form_open = (
        f'    <form class="funthenticate-form" action="{_escape(action)}" '
        f'method="{_escape(method)}"{check_action_attr}>'
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


def _prompt_body(prompt: FunPrompt, *, check_action: str | None = None) -> str:
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
        if check_action is not None:
            fields.append(_step_check_script())
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
        if check_action is not None:
            fields.append(_step_check_script())
        return "\n".join(fields)
    if prompt.drawing_template is not None:
        return "\n".join(
            (
                '      <div class="funthenticate-drawing">',
                '        <canvas class="funthenticate-canvas" '
                'aria-label="Drawing pad" role="img"></canvas>',
                '        <input class="funthenticate-drawing-strokes" '
                'name="strokes" type="hidden" value="[]">',
                '        <button class="funthenticate-secondary funthenticate-drawing-reset" '
                'type="button">Reset</button>',
                "      </div>",
                '      <button class="funthenticate-primary" type="submit">Match</button>',
                _drawing_script(),
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


def _drawing_script() -> str:
    return """
      <script>
        (() => {
          const currentScript = document.currentScript;
          const form = currentScript?.closest("form");
          const drawing = form?.querySelector(".funthenticate-drawing");
          const canvas = drawing?.querySelector(".funthenticate-canvas");
          const strokesInput = drawing?.querySelector(".funthenticate-drawing-strokes");
          const reset = drawing?.querySelector(".funthenticate-drawing-reset");
          if (!canvas || !strokesInput || !reset) {
            return;
          }

          const context = canvas.getContext("2d");
          const strokes = [];
          let currentStroke = null;

          const resize = () => {
            const rect = canvas.getBoundingClientRect();
            const ratio = window.devicePixelRatio || 1;
            canvas.width = Math.max(Math.round(rect.width * ratio), 1);
            canvas.height = Math.max(Math.round(rect.height * ratio), 1);
            context.setTransform(ratio, 0, 0, ratio, 0, 0);
            redraw();
          };

          const drawSegment = (start, end) => {
            context.strokeStyle = "#172026";
            context.lineWidth = 5;
            context.lineCap = "round";
            context.lineJoin = "round";
            context.beginPath();
            context.moveTo(start.x, start.y);
            context.lineTo(end.x, end.y);
            context.stroke();
          };

          const redraw = () => {
            const rect = canvas.getBoundingClientRect();
            context.clearRect(0, 0, rect.width, rect.height);
            for (const stroke of strokes) {
              for (let index = 1; index < stroke.length; index += 1) {
                drawSegment(stroke[index - 1], stroke[index]);
              }
            }
          };

          const pointFromEvent = (event) => {
            const rect = canvas.getBoundingClientRect();
            return {
              x: Number((event.clientX - rect.left).toFixed(2)),
              y: Number((event.clientY - rect.top).toFixed(2)),
            };
          };

          const save = () => {
            strokesInput.value = JSON.stringify(
              strokes.map((stroke) => stroke.map((point) => [point.x, point.y]))
            );
          };

          canvas.addEventListener("pointerdown", (event) => {
            event.preventDefault();
            canvas.setPointerCapture(event.pointerId);
            currentStroke = [pointFromEvent(event)];
            strokes.push(currentStroke);
            save();
          });

          canvas.addEventListener("pointermove", (event) => {
            if (!currentStroke) {
              return;
            }
            event.preventDefault();
            const point = pointFromEvent(event);
            const previous = currentStroke[currentStroke.length - 1];
            currentStroke.push(point);
            drawSegment(previous, point);
            save();
          });

          const endStroke = (event) => {
            if (!currentStroke) {
              return;
            }
            canvas.releasePointerCapture?.(event.pointerId);
            currentStroke = null;
            save();
          };

          canvas.addEventListener("pointerup", endStroke);
          canvas.addEventListener("pointercancel", endStroke);
          reset.addEventListener("click", () => {
            strokes.splice(0, strokes.length);
            currentStroke = null;
            save();
            redraw();
          });

          resize();
          window.addEventListener("resize", resize);
        })();
      </script>""".strip()


def _step_check_script() -> str:
    return """
      <script>
        (() => {
          const currentScript = document.currentScript;
          const form = currentScript?.closest("form");
          const checkAction = form?.dataset.funthenticateCheckAction;
          if (!form || !checkAction) {
            return;
          }

          const fields = Array.from(
            form.querySelectorAll('input[name="operators"], input[name="answers"]')
          );
          if (!fields.length) {
            return;
          }

          const checkField = async (field, index) => {
            const formData = new FormData();
            fields.forEach((field) => formData.append(field.name, field.value));
            formData.append("index", String(index));
            const response = await fetch(checkAction, {
              method: "POST",
              body: formData,
            });
            const data = await response.json();

            field.setCustomValidity(data.correct ? "" : data.message);
            if (data.correct) {
              field.classList.add("funthenticate-field-correct");
              field.readOnly = true;
              fields[index + 1]?.focus();
            } else {
              field.reportValidity();
            }
          };

          fields.forEach((field, index) => {
            field.addEventListener("keydown", (event) => {
              if (event.key !== "Enter") {
                return;
              }
              event.preventDefault();
              if (!field.readOnly) {
                checkField(field, index);
              }
            });
          });
        })();
      </script>""".strip()


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)
