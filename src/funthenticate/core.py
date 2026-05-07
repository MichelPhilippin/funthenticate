from __future__ import annotations

import hashlib
import math
import secrets
from collections.abc import Callable, Iterable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass, field
from numbers import Real
from typing import Protocol
from urllib.parse import urlsplit

DEFAULT_OIDC_SCOPE = "openid profile email"
DEFAULT_SESSION_KEY = "_fun_auth_mission"
_OPERATOR_GUESS_KIND_ALIASES = {
    "+": "add",
    "add": "add",
    "-": "subtract",
    "subtract": "subtract",
    "sub": "subtract",
    "*": "multiply",
    "multiply": "multiply",
    "mul": "multiply",
    "//": "integer_divide",
    "divide": "integer_divide",
    "div": "integer_divide",
    "%": "modulo",
    "modulo": "modulo",
    "mod": "modulo",
    "**": "power",
    "power": "power",
    "pow": "power",
    "identity": "identity",
    "noop": "identity",
    "keep": "identity",
}
_OPERATOR_GUESS_BASE_ALIASES = {
    "binary": 2,
    "bin": 2,
    "base2": 2,
    "octal": 8,
    "oct": 8,
    "base8": 8,
    "decimal": 10,
    "dec": 10,
    "base10": 10,
    "hexadecimal": 16,
    "hex": 16,
    "base16": 16,
}


class FunAuthError(Exception):
    """Base exception for fun authentication failures."""


class FunAuthConfigurationError(FunAuthError):
    """Raised when the wrapper or provider configuration is incomplete."""


class FunAuthDenied(FunAuthError):
    """Raised when a user cannot continue through the auth flow."""


class AuthlibOAuthRegistry(Protocol):
    def register(self, name: str, **kwargs: object) -> object:
        """Register a remote app with an Authlib-compatible registry."""


class AuthlibRemoteApp(Protocol):
    def authorize_redirect(self, redirect_uri: str, **kwargs: object) -> object:
        """Redirect the user to the upstream OAuth/OIDC provider."""

    def authorize_access_token(self, **kwargs: object) -> Mapping[str, object]:
        """Exchange the authorization response for an access token."""


@dataclass(frozen=True)
class NumberGuessMissionState:
    target: int
    attempts_used: int = 0


class FunStateStore(Protocol):
    def get_number_guess(
        self,
        mission_id: str,
        challenge_key: str,
    ) -> NumberGuessMissionState | None:
        """Return server-side state for a number guessing challenge."""

    def save_number_guess(
        self,
        mission_id: str,
        challenge_key: str,
        state: NumberGuessMissionState,
    ) -> None:
        """Persist server-side state for a number guessing challenge."""

    def clear_mission(self, mission_id: str) -> None:
        """Remove all server-side challenge state for a login mission."""


class InMemoryFunStateStore:
    def __init__(self) -> None:
        self._number_guess_states: dict[tuple[str, str], NumberGuessMissionState] = {}

    def get_number_guess(
        self,
        mission_id: str,
        challenge_key: str,
    ) -> NumberGuessMissionState | None:
        return self._number_guess_states.get((mission_id, challenge_key))

    def save_number_guess(
        self,
        mission_id: str,
        challenge_key: str,
        state: NumberGuessMissionState,
    ) -> None:
        self._number_guess_states[(mission_id, challenge_key)] = state

    def clear_mission(self, mission_id: str) -> None:
        for key in tuple(self._number_guess_states):
            if key[0] == mission_id:
                del self._number_guess_states[key]


class NextUrlValidator:
    def __call__(self, next_url: str | None) -> str | None:
        if next_url is None:
            return None
        parsed = urlsplit(next_url)
        if (
            parsed.scheme
            or parsed.netloc
            or not next_url.startswith("/")
            or next_url.startswith("//")
        ):
            raise FunAuthDenied("Next URL must be a same-site absolute path.")
        return next_url


@dataclass(frozen=True)
class OidcProvider:
    name: str
    client_id: str | None = None
    client_secret: str | None = None
    server_metadata_url: str | None = None
    authorize_url: str | None = None
    access_token_url: str | None = None
    api_base_url: str | None = None
    scope: str = DEFAULT_OIDC_SCOPE
    client_kwargs: Mapping[str, object] = field(default_factory=dict)
    extra_register_kwargs: Mapping[str, object] = field(default_factory=dict)

    def registration_kwargs(self) -> dict[str, object]:
        kwargs = dict(self.extra_register_kwargs)
        _add_if_present(kwargs, "client_id", self.client_id)
        _add_if_present(kwargs, "client_secret", self.client_secret)
        _add_if_present(kwargs, "server_metadata_url", self.server_metadata_url)
        _add_if_present(kwargs, "authorize_url", self.authorize_url)
        _add_if_present(kwargs, "access_token_url", self.access_token_url)
        _add_if_present(kwargs, "api_base_url", self.api_base_url)

        client_kwargs = dict(self.client_kwargs)
        client_kwargs.setdefault("scope", self.scope)
        kwargs["client_kwargs"] = client_kwargs
        return kwargs


@dataclass(frozen=True)
class FunPromptOption:
    key: str
    label: str
    is_correct: bool = False


@dataclass(frozen=True)
class DrawingPoint:
    x: float
    y: float


@dataclass(frozen=True)
class NormalizedDrawing:
    pixels: frozenset[tuple[int, int]]
    grid_size: int

    @property
    def is_empty(self) -> bool:
        return not self.pixels


@dataclass(frozen=True)
class DrawingMatchResult:
    score: float
    passed: bool
    template_coverage: float
    candidate_coverage: float
    message: str


@dataclass(frozen=True)
class DrawingTemplate:
    key: str
    name: str
    strokes: tuple[tuple[DrawingPoint, ...], ...]
    threshold: float = 0.68
    grid_size: int = 48
    match_radius: int = 2
    stroke_radius: int = 1
    success_message: str = "Drawing accepted."
    failure_message: str = "That drawing did not match the template closely enough."

    def compare(self, candidate_strokes: Sequence[Sequence[object]]) -> DrawingMatchResult:
        template = normalize_drawing(
            self.strokes,
            grid_size=self.grid_size,
            stroke_radius=self.stroke_radius,
        )
        candidate = normalize_drawing(
            candidate_strokes,
            grid_size=self.grid_size,
            stroke_radius=self.stroke_radius,
        )
        if template.is_empty or candidate.is_empty:
            return DrawingMatchResult(
                score=0.0,
                passed=False,
                template_coverage=0.0,
                candidate_coverage=0.0,
                message=self.failure_message,
            )

        template_coverage = _coverage(template.pixels, candidate.pixels, self.match_radius)
        candidate_coverage = _coverage(candidate.pixels, template.pixels, self.match_radius)
        score = (template_coverage + candidate_coverage) / 2
        passed = score >= self.threshold
        message = self.success_message if passed else self.failure_message
        return DrawingMatchResult(
            score=score,
            passed=passed,
            template_coverage=template_coverage,
            candidate_coverage=candidate_coverage,
            message=message,
        )


def normalize_drawing(
    strokes: Sequence[Sequence[object]],
    *,
    grid_size: int = 48,
    stroke_radius: int = 1,
) -> NormalizedDrawing:
    if grid_size < 8:
        raise FunAuthConfigurationError("Drawing grid size must be at least 8.")
    points_by_stroke = tuple(
        tuple(_coerce_drawing_point(point) for point in stroke) for stroke in strokes if stroke
    )
    points = [point for stroke in points_by_stroke for point in stroke]
    if not points:
        return NormalizedDrawing(pixels=frozenset(), grid_size=grid_size)

    min_x = min(point.x for point in points)
    max_x = max(point.x for point in points)
    min_y = min(point.y for point in points)
    max_y = max(point.y for point in points)
    width = max_x - min_x
    height = max_y - min_y
    longest_side = max(width, height)
    if longest_side <= 0:
        return NormalizedDrawing(pixels=frozenset(), grid_size=grid_size)

    margin = max(2, grid_size // 12)
    drawable_size = grid_size - (margin * 2) - 1
    scale = drawable_size / longest_side
    scaled_width = width * scale
    scaled_height = height * scale
    offset_x = (grid_size - 1 - scaled_width) / 2
    offset_y = (grid_size - 1 - scaled_height) / 2

    normalized_strokes = tuple(
        tuple(
            DrawingPoint(
                x=(point.x - min_x) * scale + offset_x,
                y=(point.y - min_y) * scale + offset_y,
            )
            for point in stroke
        )
        for stroke in points_by_stroke
    )
    pixels = _rasterize_strokes(normalized_strokes, grid_size, stroke_radius)
    return NormalizedDrawing(pixels=frozenset(pixels), grid_size=grid_size)


@dataclass(frozen=True)
class ConversionStep:
    kind: str
    operand: int | None = None
    output_base: int = 10
    label: str | None = None

    def apply(self, value: int) -> tuple[int, str, int]:
        kind = self.kind.lower().strip()
        output_base = self.resolved_output_base()
        if kind in {"add", "+"}:
            next_value = value + _required_operand(self)
            return next_value, self.description(value, next_value), output_base
        if kind in {"subtract", "sub", "-"}:
            next_value = value - _required_operand(self)
            return next_value, self.description(value, next_value), output_base
        if kind in {"multiply", "mul", "*"}:
            next_value = value * _required_operand(self)
            return next_value, self.description(value, next_value), output_base
        if kind in {"integer_divide", "divide", "div", "//"}:
            operand = _required_operand(self)
            if operand == 0:
                raise FunAuthConfigurationError("Conversion challenge cannot divide by zero.")
            next_value = value // operand
            return next_value, self.description(value, next_value), output_base
        if kind in {"modulo", "mod", "%"}:
            operand = _required_operand(self)
            if operand == 0:
                raise FunAuthConfigurationError("Conversion challenge cannot modulo by zero.")
            next_value = value % operand
            return next_value, self.description(value, next_value), output_base
        if kind in {"power", "pow", "**"}:
            next_value = value ** _required_operand(self)
            return next_value, self.description(value, next_value), output_base
        if kind in {"identity", "noop", "keep"}:
            return value, self.description(value, value), output_base
        if kind in {"to_binary", "to_bin", "binary", "bin"}:
            return value, self.description(value, value), 2
        if kind in {"to_hex", "hex", "hexadecimal"}:
            return value, self.description(value, value), 16
        if kind in {"to_octal", "to_oct", "octal", "oct"}:
            return value, self.description(value, value), 8
        raise FunAuthConfigurationError(f"Unknown conversion step kind: {self.kind}")

    def description(self, value: int, next_value: int) -> str:
        if self.label is not None:
            return self.label
        kind = self.kind.lower().strip()
        if kind in {"add", "+"}:
            operation = f"Add {_required_operand(self)}"
        elif kind in {"subtract", "sub", "-"}:
            operation = f"Subtract {_required_operand(self)}"
        elif kind in {"multiply", "mul", "*"}:
            operation = f"Multiply by {_required_operand(self)}"
        elif kind in {"integer_divide", "divide", "div", "//"}:
            operation = f"Integer divide by {_required_operand(self)}"
        elif kind in {"modulo", "mod", "%"}:
            operation = f"Modulo {_required_operand(self)}"
        elif kind in {"power", "pow", "**"}:
            operation = f"Raise to power {_required_operand(self)}"
        elif kind in {"identity", "noop", "keep"}:
            operation = "Keep the current value"
        elif kind in {"to_binary", "to_bin", "binary", "bin"}:
            return "Convert to binary"
        elif kind in {"to_hex", "hex", "hexadecimal"}:
            return "Convert to hexadecimal"
        elif kind in {"to_octal", "to_oct", "octal", "oct"}:
            return "Convert to octal"
        else:
            operation = f"Apply {self.kind} to {value} -> {next_value}"

        conversion = _conversion_description(self.resolved_output_base())
        if conversion is None:
            return operation
        return f"{operation}, then convert to {conversion}"

    def resolved_output_base(self) -> int:
        return _validate_output_base(self.output_base)


@dataclass(frozen=True)
class ConversionStepResult:
    index: int
    label: str
    expected: str
    submitted: str | None
    correct: bool
    numeric_value: int
    output_base: int


@dataclass(frozen=True)
class ConversionChallengeResult:
    passed: bool
    step_results: tuple[ConversionStepResult, ...]
    final_value: int | None
    message: str

    @property
    def correct_count(self) -> int:
        return sum(1 for result in self.step_results if result.correct)


@dataclass(frozen=True)
class ConversionOperatorGuessStepResult:
    index: int
    displayed_from: str
    displayed_to: str
    submitted_move: str | None
    expected_move: str
    correct: bool
    computed_value: int | None
    expected_value: int
    message: str | None = None


@dataclass(frozen=True)
class ConversionOperatorGuessResult:
    passed: bool
    step_results: tuple[ConversionOperatorGuessStepResult, ...]
    display_values: tuple[str, ...]
    message: str

    @property
    def correct_count(self) -> int:
        return sum(1 for result in self.step_results if result.correct)


@dataclass(frozen=True)
class ConversionChallenge:
    key: str
    name: str
    start_value: int
    steps: tuple[ConversionStep, ...]
    success_message: str = "Conversion lock opened."
    failure_message: str = "Some conversion steps need another pass."

    def __post_init__(self) -> None:
        if not self.steps:
            raise FunAuthConfigurationError("ConversionChallenge needs at least one step.")

    def evaluate(self, answers: Sequence[object]) -> ConversionChallengeResult:
        value = self.start_value
        results: list[ConversionStepResult] = []
        for index, step in enumerate(self.steps, start=1):
            value, label, output_base = step.apply(value)
            expected = format_number_for_base(value, output_base)
            submitted = _optional_string(answers[index - 1]) if index <= len(answers) else None
            correct = submitted is not None and _answer_matches_number(
                submitted,
                value,
                output_base,
            )
            results.append(
                ConversionStepResult(
                    index=index,
                    label=label,
                    expected=expected,
                    submitted=submitted,
                    correct=correct,
                    numeric_value=value,
                    output_base=output_base,
                )
            )

        passed = len(answers) == len(self.steps) and all(result.correct for result in results)
        return ConversionChallengeResult(
            passed=passed,
            step_results=tuple(results),
            final_value=value,
            message=self.success_message if passed else self.failure_message,
        )


@dataclass(frozen=True)
class ConversionOperatorGuessChallenge:
    key: str
    name: str
    start_value: int
    steps: tuple[ConversionStep, ...]
    start_base: int = 10
    success_message: str = "Operator lock opened."
    failure_message: str = "Some operators do not connect the shown numbers."

    def __post_init__(self) -> None:
        if not self.steps:
            raise FunAuthConfigurationError("ConversionOperatorGuessChallenge needs steps.")
        _validate_output_base(self.start_base)

    def display_values(self) -> tuple[str, ...]:
        value = self.start_value
        values = [format_number_for_base(value, self.start_base)]
        for step in self.steps:
            value, _label, output_base = step.apply(value)
            values.append(format_number_for_base(value, output_base))
        return tuple(values)

    def evaluate(self, operators: Sequence[object]) -> ConversionOperatorGuessResult:
        value = self.start_value
        display_values = self.display_values()
        results: list[ConversionOperatorGuessStepResult] = []
        for index, step in enumerate(self.steps, start=1):
            expected_value, _label, _output_base = step.apply(value)
            submitted = _optional_string(operators[index - 1]) if index <= len(operators) else None
            computed_value = None
            correct = False
            step_message = None
            if submitted is not None:
                try:
                    submitted_step = parse_conversion_operator(submitted, template_step=step)
                    computed_value, _submitted_label, submitted_base = submitted_step.apply(value)
                    correct = (
                        computed_value == expected_value
                        and submitted_base == step.resolved_output_base()
                    )
                except FunAuthError as error:
                    correct = False
                    step_message = str(error)
            results.append(
                ConversionOperatorGuessStepResult(
                    index=index,
                    displayed_from=display_values[index - 1],
                    displayed_to=display_values[index],
                    submitted_move=submitted,
                    expected_move=_operator_hint(step),
                    correct=correct,
                    computed_value=computed_value,
                    expected_value=expected_value,
                    message=step_message,
                )
            )
            value = expected_value

        passed = len(operators) == len(self.steps) and all(result.correct for result in results)
        message = self.success_message if passed else self.failure_message
        tease = next((result.message for result in results if result.message), None)
        if not passed and tease is not None:
            message = tease
        return ConversionOperatorGuessResult(
            passed=passed,
            step_results=tuple(results),
            display_values=display_values,
            message=message,
        )


def build_conversion_challenge(
    *,
    start_value: int,
    steps: Sequence[ConversionStep | Mapping[str, object]],
    step_count: int | None = None,
    key: str = "conversion-lock",
    name: str = "Conversion lock",
) -> ConversionChallenge:
    selected_steps = tuple(_coerce_conversion_step(step) for step in steps)
    if step_count is not None:
        if step_count < 1:
            raise FunAuthConfigurationError("Conversion challenge step count must be positive.")
        selected_steps = selected_steps[:step_count]
    return ConversionChallenge(key=key, name=name, start_value=start_value, steps=selected_steps)


def build_conversion_operator_guess_challenge(
    *,
    start_value: int,
    steps: Sequence[ConversionStep | Mapping[str, object]],
    step_count: int | None = None,
    key: str = "operator-conversion-lock",
    name: str = "Operator conversion lock",
    start_base: int = 10,
) -> ConversionOperatorGuessChallenge:
    selected_steps = tuple(_coerce_conversion_step(step) for step in steps)
    if step_count is not None:
        if step_count < 1:
            raise FunAuthConfigurationError("Operator guess step count must be positive.")
        selected_steps = selected_steps[:step_count]
    return ConversionOperatorGuessChallenge(
        key=key,
        name=name,
        start_value=start_value,
        steps=selected_steps,
        start_base=start_base,
    )


def parse_conversion_operator(
    value: str,
    *,
    template_step: ConversionStep | None = None,
) -> ConversionStep:
    tokens = _operator_guess_tokens(value)
    kind = "identity"
    output_base = 10
    saw_operation = False
    saw_conversion = False

    for token in tokens:
        if token in _OPERATOR_GUESS_KIND_ALIASES:
            if saw_operation:
                raise FunAuthDenied("Only one operator is allowed per conversion guess.")
            kind = _OPERATOR_GUESS_KIND_ALIASES[token]
            saw_operation = True
            continue
        if token in _OPERATOR_GUESS_BASE_ALIASES:
            if saw_conversion:
                raise FunAuthDenied("Only one base conversion is allowed per conversion guess.")
            output_base = _OPERATOR_GUESS_BASE_ALIASES[token]
            saw_conversion = True
            continue
        raise FunAuthDenied("Operator guesses may only contain operators or base conversions.")

    if not saw_operation and not saw_conversion:
        raise FunAuthDenied("Operator guesses need an operator or base conversion.")

    operand = None
    if kind not in {"identity", "to_binary", "to_hex", "to_octal"}:
        if template_step is None:
            raise FunAuthDenied("Operator guesses do not include numbers; provide a template step.")
        operand = _required_operand(template_step)
    return ConversionStep(kind, operand, output_base=output_base)


def format_number_for_base(value: int, base: int) -> str:
    prefix_by_base = {2: "0b", 8: "0o", 10: "", 16: "0x"}
    if base not in prefix_by_base:
        raise FunAuthConfigurationError(f"Unsupported output base: {base}")
    sign = "-" if value < 0 else ""
    magnitude = abs(value)
    if base == 10:
        return f"{value}"
    if base == 2:
        digits = format(magnitude, "b")
    elif base == 8:
        digits = format(magnitude, "o")
    else:
        digits = format(magnitude, "x")
    return f"{sign}{prefix_by_base[base]}{digits}"


@dataclass(frozen=True)
class PopupChallenge:
    key: str
    message: str = "I'm authorized"
    confirm_label: str = "OK"
    success_message: str = "Authorization acknowledged."
    failure_message: str = "Authorization popup was not acknowledged."

    def acknowledge(self, accepted: bool) -> FunChallengeResult:
        return FunChallengeResult(
            prompt=FunPrompt(
                key=self.key,
                title="Authorization Popup",
                prompt=self.message,
                options=(),
                success_message=self.success_message,
                failure_message=self.failure_message,
                popup=self,
            ),
            answer_key=self.key if accepted else "dismissed",
            passed=accepted,
            message=self.success_message if accepted else self.failure_message,
        )


@dataclass(frozen=True)
class NumberGuessResult:
    guess: int
    passed: bool
    attempts_used: int
    attempts_remaining: int
    range_min: int
    range_max: int
    hint: str
    message: str
    reset: bool = False


@dataclass(frozen=True)
class NumberGuessChallenge:
    key: str
    range_min: int = 1
    range_max: int = 10
    max_tries: int = 3
    success_message: str = "Correct. The number lock opened."
    failure_message: str = (
        "You failed to guess the number in time. This is your failure; the game has reset."
    )

    def __post_init__(self) -> None:
        if self.range_min > self.range_max:
            raise FunAuthConfigurationError("Number guess range_min must be <= range_max.")
        if self.max_tries < 1:
            raise FunAuthConfigurationError("Number guess max_tries must be positive.")

    def choose_target(self) -> int:
        return self.range_min + secrets.randbelow(self.range_max - self.range_min + 1)

    def evaluate(self, guess: int, target: int, attempts_used: int) -> NumberGuessResult:
        if guess < self.range_min or guess > self.range_max:
            raise FunAuthDenied("Guess is outside the configured range.")

        current_attempts = attempts_used + 1
        if guess == target:
            return NumberGuessResult(
                guess=guess,
                passed=True,
                attempts_used=current_attempts,
                attempts_remaining=self.max_tries - current_attempts,
                range_min=self.range_min,
                range_max=self.range_max,
                hint="correct",
                message=self.success_message,
            )

        attempts_remaining = self.max_tries - current_attempts
        if attempts_remaining <= 0:
            return NumberGuessResult(
                guess=guess,
                passed=False,
                attempts_used=current_attempts,
                attempts_remaining=0,
                range_min=self.range_min,
                range_max=self.range_max,
                hint="too-low" if guess < target else "too-high",
                message=self.failure_message,
                reset=True,
            )

        direction = "too low" if guess < target else "too high"
        return NumberGuessResult(
            guess=guess,
            passed=False,
            attempts_used=current_attempts,
            attempts_remaining=attempts_remaining,
            range_min=self.range_min,
            range_max=self.range_max,
            hint="too-low" if guess < target else "too-high",
            message=f"Nope. {guess} is {direction}. {attempts_remaining} tries left.",
        )


@dataclass(frozen=True)
class FunPrompt:
    key: str
    title: str
    prompt: str
    options: tuple[FunPromptOption, ...]
    success_message: str
    failure_message: str
    drawing_template: DrawingTemplate | None = None
    conversion_challenge: ConversionChallenge | None = None
    operator_guess_challenge: ConversionOperatorGuessChallenge | None = None
    popup: PopupChallenge | None = None
    number_guess: NumberGuessChallenge | None = None

    def evaluate(self, answer_key: str) -> FunChallengeResult:
        valid_keys = {option.key for option in self.options}
        correct_keys = {option.key for option in self.options if option.is_correct}
        passed = answer_key in correct_keys if correct_keys else answer_key in valid_keys
        message = self.success_message if passed else self.failure_message
        return FunChallengeResult(
            prompt=self,
            answer_key=answer_key,
            passed=passed,
            message=message,
        )


@dataclass(frozen=True)
class FunChallengeResult:
    prompt: FunPrompt
    answer_key: str
    passed: bool
    message: str


@dataclass(frozen=True)
class FunLoginMission:
    provider_name: str | None
    prompt: FunPrompt
    next_url: str | None
    prompt_index: int = 0
    prompt_count: int = 1


FunMission = FunLoginMission


@dataclass(frozen=True)
class FunAuthIdentity:
    provider_name: str
    subject: str
    display_name: str
    email: str | None = None
    avatar_url: str | None = None
    raw_claims: Mapping[str, object] = field(default_factory=dict)

    def to_session(self) -> dict[str, object]:
        return {
            "provider_name": self.provider_name,
            "subject": self.subject,
            "display_name": self.display_name,
            "email": self.email,
            "avatar_url": self.avatar_url,
        }


@dataclass(frozen=True)
class FunWelcome:
    headline: str
    detail: str
    badge: str


@dataclass(frozen=True)
class FunLoginResult:
    identity: FunAuthIdentity
    token: Mapping[str, object]
    welcome: FunWelcome
    next_url: str | None
    prompt_key: str | None


FunAuthResult = FunLoginResult


@dataclass(frozen=True)
class FunOnlyResult:
    next_url: str | None
    prompt_keys: tuple[str, ...]
    welcome: FunWelcome


@dataclass(frozen=True)
class FunMissionState:
    mission_id: str
    provider_name: str | None
    prompt_keys: tuple[str, ...]
    prompt_index: int
    prompt_key: str
    answer_key: str | None
    challenge_passed: bool
    fun_complete: bool
    next_url: str | None

    @classmethod
    def create(
        cls,
        *,
        provider_name: str | None,
        prompt_keys: Sequence[str],
        next_url: str | None,
    ) -> FunMissionState:
        keys = tuple(prompt_keys)
        if not keys:
            raise FunAuthConfigurationError("Mission state needs at least one prompt.")
        return cls(
            mission_id=secrets.token_urlsafe(16),
            provider_name=provider_name,
            prompt_keys=keys,
            prompt_index=0,
            prompt_key=keys[0],
            answer_key=None,
            challenge_passed=False,
            fun_complete=False,
            next_url=next_url,
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> FunMissionState:
        prompt_keys = _prompt_keys_from_state(value)
        prompt_index = _prompt_index(value)
        if prompt_index >= len(prompt_keys):
            raise FunAuthDenied("Login mission state has an invalid prompt index.")
        return cls(
            mission_id=_mission_id(value),
            provider_name=_optional_string(value.get("provider_name")),
            prompt_keys=prompt_keys,
            prompt_index=prompt_index,
            prompt_key=prompt_keys[prompt_index],
            answer_key=_optional_string(value.get("answer_key")),
            challenge_passed=bool(value.get("challenge_passed")),
            fun_complete=bool(value.get("fun_complete")),
            next_url=_optional_string(value.get("next_url")),
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "mission_id": self.mission_id,
            "provider_name": self.provider_name,
            "prompt_keys": list(self.prompt_keys),
            "prompt_index": self.prompt_index,
            "prompt_key": self.prompt_key,
            "answer_key": self.answer_key,
            "challenge_passed": self.challenge_passed,
            "fun_complete": self.fun_complete,
            "next_url": self.next_url,
        }

    def as_mission(self, prompt: FunPrompt) -> FunMission:
        return FunMission(
            provider_name=self.provider_name,
            prompt=prompt,
            next_url=self.next_url,
            prompt_index=self.prompt_index,
            prompt_count=len(self.prompt_keys),
        )

    def advance_after_answer(self, passed: bool) -> FunMissionState:
        if not passed:
            return self
        next_index = self.prompt_index + 1
        if next_index < len(self.prompt_keys):
            return FunMissionState(
                mission_id=self.mission_id,
                provider_name=self.provider_name,
                prompt_keys=self.prompt_keys,
                prompt_index=next_index,
                prompt_key=self.prompt_keys[next_index],
                answer_key=None,
                challenge_passed=False,
                fun_complete=False,
                next_url=self.next_url,
            )
        return FunMissionState(
            mission_id=self.mission_id,
            provider_name=self.provider_name,
            prompt_keys=self.prompt_keys,
            prompt_index=self.prompt_index,
            prompt_key=self.prompt_key,
            answer_key=self.answer_key,
            challenge_passed=True,
            fun_complete=True,
            next_url=self.next_url,
        )


@dataclass(frozen=True)
class FunAuthIdea:
    key: str
    name: str
    description: str
    security_role: str


class PromptDeck:
    def __init__(self, prompts: Sequence[FunPrompt]) -> None:
        if not prompts:
            raise FunAuthConfigurationError("PromptDeck needs at least one prompt.")
        self._prompts_by_key = {prompt.key: prompt for prompt in prompts}
        if len(self._prompts_by_key) != len(prompts):
            raise FunAuthConfigurationError("Prompt keys must be unique.")
        self._prompts = tuple(prompts)

    def choose(self, prompt_key: str | None = None) -> FunPrompt:
        if prompt_key is not None:
            return self.get(prompt_key)
        return secrets.choice(self._prompts)

    def get(self, prompt_key: str) -> FunPrompt:
        try:
            return self._prompts_by_key[prompt_key]
        except KeyError as error:
            raise FunAuthConfigurationError(f"Unknown prompt: {prompt_key}") from error

    def all(self) -> tuple[FunPrompt, ...]:
        return self._prompts


class FunAuth:
    def __init__(
        self,
        oauth: AuthlibOAuthRegistry | None = None,
        *,
        prompt_deck: PromptDeck | None = None,
        session_key: str = DEFAULT_SESSION_KEY,
        trusted_email_domains: Sequence[str] = (),
        require_prompt_passed: bool = True,
        state_store: FunStateStore | None = None,
        next_url_validator: Callable[[str | None], str | None] | None = None,
    ) -> None:
        self._oauth = oauth
        self._prompt_deck = prompt_deck or PromptDeck(default_fun_prompts())
        self._session_key = session_key
        self._trusted_email_domains = frozenset(
            _normalize_domain(domain) for domain in trusted_email_domains
        )
        self._require_prompt_passed = require_prompt_passed
        self._state_store = state_store or InMemoryFunStateStore()
        self._next_url_validator = next_url_validator or NextUrlValidator()

    @classmethod
    def for_flask_app(cls, app: object, **kwargs: object) -> FunAuth:
        from authlib.integrations.flask_client import OAuth

        return cls(OAuth(app), **kwargs)

    def register_provider(self, provider: OidcProvider) -> object:
        if self._oauth is None:
            raise FunAuthConfigurationError(
                "Provider registration needs an Authlib OAuth registry."
            )
        return self._oauth.register(provider.name, **provider.registration_kwargs())

    def prepare_login(
        self,
        session: MutableMapping[str, object],
        provider_name: str | None = None,
        *,
        next_url: str | None = None,
        prompt_key: str | None = None,
        prompt_keys: Sequence[str] | None = None,
    ) -> FunLoginMission:
        selected_prompt_keys = self._selected_prompt_keys(prompt_key, prompt_keys)
        prompt = self._prompt_deck.get(selected_prompt_keys[0])
        validated_next_url = self._next_url_validator(next_url)
        mission_state = FunMissionState.create(
            provider_name=provider_name,
            prompt_keys=selected_prompt_keys,
            next_url=validated_next_url,
        )
        session[self._session_key] = mission_state.to_mapping()
        return mission_state.as_mission(prompt)

    def prepare_mission(
        self,
        session: MutableMapping[str, object],
        provider_name: str | None = None,
        *,
        next_url: str | None = None,
        prompt_key: str | None = None,
        prompt_keys: Sequence[str] | None = None,
    ) -> FunMission:
        return self.prepare_login(
            session,
            provider_name,
            next_url=next_url,
            prompt_key=prompt_key,
            prompt_keys=prompt_keys,
        )

    def current_mission(self, session: MutableMapping[str, object]) -> FunLoginMission:
        mission_state = self._mission_state(session)
        typed_state = FunMissionState.from_mapping(mission_state)
        prompt = self._prompt_deck.get(typed_state.prompt_key)
        return typed_state.as_mission(prompt)

    def answer_prompt(
        self,
        session: MutableMapping[str, object],
        answer_key: str,
    ) -> FunChallengeResult:
        mission_state = self._mission_state(session)
        prompt = self._prompt_deck.get(str(mission_state["prompt_key"]))
        result = prompt.evaluate(answer_key)

        mission_state["answer_key"] = answer_key
        mission_state["challenge_passed"] = result.passed
        mission_state["challenge_message"] = result.message
        self._advance_or_save_mission(session, mission_state, result.passed)
        return result

    def answer_drawing(
        self,
        session: MutableMapping[str, object],
        strokes: Sequence[Sequence[object]],
    ) -> DrawingMatchResult:
        mission_state = self._mission_state(session)
        prompt = self._prompt_deck.get(str(mission_state["prompt_key"]))
        if prompt.drawing_template is None:
            raise FunAuthDenied("This login prompt does not accept a drawing.")

        result = prompt.drawing_template.compare(strokes)
        mission_state["answer_key"] = prompt.drawing_template.key
        mission_state["challenge_passed"] = result.passed
        mission_state["challenge_message"] = result.message
        mission_state["drawing_score"] = result.score
        self._advance_or_save_mission(session, mission_state, result.passed)
        return result

    def answer_conversion(
        self,
        session: MutableMapping[str, object],
        answers: Sequence[object],
    ) -> ConversionChallengeResult:
        mission_state = self._mission_state(session)
        prompt = self._prompt_deck.get(str(mission_state["prompt_key"]))
        if prompt.conversion_challenge is None:
            raise FunAuthDenied("This login prompt does not accept conversion answers.")

        result = prompt.conversion_challenge.evaluate(answers)
        mission_state["answer_key"] = prompt.conversion_challenge.key
        mission_state["challenge_passed"] = result.passed
        mission_state["challenge_message"] = result.message
        mission_state["conversion_correct_count"] = result.correct_count
        mission_state["conversion_step_count"] = len(result.step_results)
        self._advance_or_save_mission(session, mission_state, result.passed)
        return result

    def answer_conversion_operators(
        self,
        session: MutableMapping[str, object],
        operators: Sequence[object],
    ) -> ConversionOperatorGuessResult:
        mission_state = self._mission_state(session)
        prompt = self._prompt_deck.get(str(mission_state["prompt_key"]))
        if prompt.operator_guess_challenge is None:
            raise FunAuthDenied("This login prompt does not accept conversion operators.")

        result = prompt.operator_guess_challenge.evaluate(operators)
        mission_state["answer_key"] = prompt.operator_guess_challenge.key
        mission_state["challenge_passed"] = result.passed
        mission_state["challenge_message"] = result.message
        mission_state["conversion_operator_correct_count"] = result.correct_count
        mission_state["conversion_operator_step_count"] = len(result.step_results)
        self._advance_or_save_mission(session, mission_state, result.passed)
        return result

    def answer_popup(
        self,
        session: MutableMapping[str, object],
        *,
        accepted: bool = True,
    ) -> FunChallengeResult:
        mission_state = self._mission_state(session)
        prompt = self._prompt_deck.get(str(mission_state["prompt_key"]))
        if prompt.popup is None:
            raise FunAuthDenied("This login prompt does not accept popup acknowledgement.")

        result = FunChallengeResult(
            prompt=prompt,
            answer_key=prompt.popup.key if accepted else "dismissed",
            passed=accepted,
            message=prompt.success_message if accepted else prompt.failure_message,
        )
        mission_state["answer_key"] = result.answer_key
        mission_state["challenge_passed"] = result.passed
        mission_state["challenge_message"] = result.message
        self._advance_or_save_mission(session, mission_state, result.passed)
        return result

    def answer_number_guess(
        self,
        session: MutableMapping[str, object],
        guess: int,
    ) -> NumberGuessResult:
        mission_state = self._mission_state(session)
        prompt = self._prompt_deck.get(str(mission_state["prompt_key"]))
        if prompt.number_guess is None:
            raise FunAuthDenied("This login prompt does not accept number guesses.")

        mission_id = _mission_id(mission_state)
        challenge_state = self._state_store.get_number_guess(mission_id, prompt.number_guess.key)
        if challenge_state is None:
            challenge_state = NumberGuessMissionState(target=prompt.number_guess.choose_target())

        result = prompt.number_guess.evaluate(
            guess,
            challenge_state.target,
            challenge_state.attempts_used,
        )
        mission_state["answer_key"] = prompt.number_guess.key
        mission_state["challenge_passed"] = result.passed
        mission_state["challenge_message"] = result.message
        mission_state["number_guess_hint"] = result.hint
        mission_state["number_guess_attempts_remaining"] = result.attempts_remaining
        if result.passed:
            next_challenge_state = NumberGuessMissionState(
                target=challenge_state.target,
                attempts_used=result.attempts_used,
            )
        elif result.reset:
            next_challenge_state = NumberGuessMissionState(
                target=prompt.number_guess.choose_target(),
                attempts_used=0,
            )
        else:
            next_challenge_state = NumberGuessMissionState(
                target=challenge_state.target,
                attempts_used=result.attempts_used,
            )
        self._state_store.save_number_guess(
            mission_id, prompt.number_guess.key, next_challenge_state
        )
        self._advance_or_save_mission(session, mission_state, result.passed)
        return result

    def redirect_to_provider(
        self,
        session: MutableMapping[str, object],
        redirect_uri: str,
        **authorize_kwargs: object,
    ) -> object:
        mission_state = self._mission_state(session)
        if self._require_prompt_passed and not mission_state.get("challenge_passed"):
            raise FunAuthDenied("Finish the login prompt before starting provider auth.")

        provider_name = _optional_string(mission_state.get("provider_name"))
        if provider_name is None:
            raise FunAuthDenied("This fun mission does not have a provider auth step.")
        client = self._client(provider_name)
        return client.authorize_redirect(redirect_uri, **authorize_kwargs)

    def complete_fun(self, session: MutableMapping[str, object]) -> FunOnlyResult:
        mission_state = self._mission_state(session)
        if not mission_state.get("challenge_passed"):
            raise FunAuthDenied("Finish the fun prompts before completing the mission.")

        result = FunOnlyResult(
            next_url=_optional_string(mission_state.get("next_url")),
            prompt_keys=tuple(_prompt_keys_from_state(mission_state)),
            welcome=FunWelcome(
                headline="Fun gate cleared",
                detail="The ritual is complete; no provider auth was requested.",
                badge="Certified Fun",
            ),
        )
        session.pop(self._session_key, None)
        mission_id = _optional_string(mission_state.get("mission_id"))
        if mission_id is not None:
            self._state_store.clear_mission(mission_id)
        return result

    def complete_login(
        self,
        session: MutableMapping[str, object],
        *,
        provider_name: str | None = None,
        **token_kwargs: object,
    ) -> FunLoginResult:
        mission_state = self._mission_state(session, required=False)
        if self._require_prompt_passed and not mission_state.get("challenge_passed"):
            raise FunAuthDenied("Finish the login prompt before completing provider auth.")
        resolved_provider = provider_name or _optional_string(mission_state.get("provider_name"))
        if resolved_provider is None:
            raise FunAuthDenied("Cannot complete login without a provider name.")

        client = self._client(resolved_provider)
        token = dict(client.authorize_access_token(**token_kwargs))
        identity = self.extract_identity(resolved_provider, token)
        self._validate_identity(identity)

        prompt_key = _optional_string(mission_state.get("prompt_key"))
        result = FunLoginResult(
            identity=identity,
            token=token,
            welcome=self.welcome_for(identity),
            next_url=_optional_string(mission_state.get("next_url")),
            prompt_key=prompt_key,
        )
        session.pop(self._session_key, None)
        mission_id = _optional_string(mission_state.get("mission_id"))
        if mission_id is not None:
            self._state_store.clear_mission(mission_id)
        return result

    def extract_identity(
        self,
        provider_name: str,
        token: Mapping[str, object],
    ) -> FunAuthIdentity:
        claims = _claims_from_token(token)
        subject = _first_string(claims, "sub", "id", "oid", "email")
        if subject is None:
            raise FunAuthDenied("The auth provider did not return a stable user id.")

        email = _first_string(claims, "email", "preferred_username", "upn")
        display_name = _first_string(claims, "name", "given_name", "preferred_username", "email")
        avatar_url = _first_string(claims, "picture", "avatar_url")
        return FunAuthIdentity(
            provider_name=provider_name,
            subject=subject,
            display_name=display_name or subject,
            email=email,
            avatar_url=avatar_url,
            raw_claims=claims,
        )

    def welcome_for(self, identity: FunAuthIdentity) -> FunWelcome:
        badges = (
            "Focus Pilot",
            "Calendar Cartographer",
            "Agenda Minimalist",
            "Room Timekeeper",
            "Deep Work Defender",
        )
        badge = badges[_stable_index(identity.subject, len(badges))]
        return FunWelcome(
            headline=f"Welcome back, {identity.display_name}",
            detail="You cleared the fun gate; Authlib handled the serious part.",
            badge=badge,
        )

    def _client(self, provider_name: str) -> AuthlibRemoteApp:
        if self._oauth is None:
            raise FunAuthConfigurationError("Provider auth needs an Authlib OAuth registry.")
        create_client = getattr(self._oauth, "create_client", None)
        if callable(create_client):
            client = create_client(provider_name)
            if client is not None:
                return client

        try:
            return getattr(self._oauth, provider_name)
        except AttributeError as error:
            raise FunAuthConfigurationError(
                f"Provider {provider_name!r} is not registered."
            ) from error

    def _selected_prompt_keys(
        self,
        prompt_key: str | None,
        prompt_keys: Sequence[str] | None,
    ) -> tuple[str, ...]:
        if prompt_key is not None and prompt_keys is not None:
            raise FunAuthConfigurationError("Use either prompt_key or prompt_keys, not both.")
        if prompt_keys is not None:
            keys = tuple(str(key) for key in prompt_keys)
            if not keys:
                raise FunAuthConfigurationError("Prompt sequence needs at least one prompt.")
            for key in keys:
                self._prompt_deck.get(key)
            return keys
        return (self._prompt_deck.choose(prompt_key).key,)

    def _advance_or_save_mission(
        self,
        session: MutableMapping[str, object],
        mission_state: dict[str, object],
        passed: bool,
    ) -> None:
        typed_state = FunMissionState.from_mapping(mission_state).advance_after_answer(passed)
        if not passed:
            session[self._session_key] = mission_state
            return
        next_mapping = typed_state.to_mapping()
        for key, value in mission_state.items():
            next_mapping.setdefault(key, value)
        session[self._session_key] = next_mapping

    def _mission_state(
        self,
        session: MutableMapping[str, object],
        *,
        required: bool = True,
    ) -> dict[str, object]:
        raw_state = session.get(self._session_key)
        if raw_state is None:
            if required:
                raise FunAuthDenied("No login mission is active.")
            return {}
        if not isinstance(raw_state, dict):
            raise FunAuthDenied("Login mission state is invalid.")
        return dict(raw_state)

    def _validate_identity(self, identity: FunAuthIdentity) -> None:
        if not self._trusted_email_domains:
            return
        if identity.email is None:
            raise FunAuthDenied("This login provider did not return an email address.")

        domain = _domain_from_email(identity.email)
        if domain not in self._trusted_email_domains:
            raise FunAuthDenied("This email domain is not allowed for this application.")


def default_fun_prompts() -> tuple[FunPrompt, ...]:
    return (
        FunPrompt(
            key="draw-key",
            title="Draw the Key",
            prompt="Draw the key shape to unlock this login.",
            options=(),
            success_message="The key fits. Drawing accepted.",
            failure_message="That key is a little too mysterious. Try the same basic outline.",
            drawing_template=default_key_drawing_template(),
        ),
        FunPrompt(
            key="conversion-lock",
            title="Conversion Lock",
            prompt="Solve each operation and base conversion in sequence.",
            options=(),
            success_message="Conversion lock opened.",
            failure_message="Some conversion steps need another pass.",
            conversion_challenge=default_conversion_challenge(),
        ),
        FunPrompt(
            key="operator-conversion-lock",
            title="Operator Conversion Lock",
            prompt="Infer the operators and base conversions that connect the shown numbers.",
            options=(),
            success_message="Operator lock opened.",
            failure_message="Some operators do not connect the shown numbers.",
            operator_guess_challenge=default_conversion_operator_guess_challenge(),
        ),
        FunPrompt(
            key="number-guess",
            title="Number Guess",
            prompt="Guess the secret number before your tries run out.",
            options=(),
            success_message="Correct. The number lock opened.",
            failure_message=(
                "You failed to guess the number in time. This is your failure; the game has reset."
            ),
            number_guess=NumberGuessChallenge(
                key="number-guess", range_min=1, range_max=10, max_tries=3
            ),
        ),
        FunPrompt(
            key="authorized-popup",
            title="Authorization Popup",
            prompt="I'm authorized",
            options=(),
            success_message="Authorization acknowledged.",
            failure_message="Authorization popup was not acknowledged.",
            popup=PopupChallenge(key="authorized-popup"),
        ),
    )


def default_conversion_steps() -> tuple[ConversionStep, ...]:
    return (
        ConversionStep("add", 5, output_base=16),
        ConversionStep("multiply", 3, output_base=2),
        ConversionStep("subtract", 7, output_base=8),
    )


def default_conversion_challenge(step_count: int = 3) -> ConversionChallenge:
    return build_conversion_challenge(
        start_value=13,
        steps=default_conversion_steps(),
        step_count=step_count,
    )


def default_conversion_operator_guess_challenge(
    step_count: int = 3,
) -> ConversionOperatorGuessChallenge:
    return build_conversion_operator_guess_challenge(
        start_value=13,
        steps=default_conversion_steps(),
        step_count=step_count,
    )


def default_key_drawing_template() -> DrawingTemplate:
    return DrawingTemplate(
        key="simple-key",
        name="Simple key",
        strokes=(
            (
                DrawingPoint(0.0, 0.0),
                DrawingPoint(1.0, -0.8),
                DrawingPoint(2.2, -0.8),
                DrawingPoint(3.2, 0.0),
                DrawingPoint(2.2, 0.8),
                DrawingPoint(1.0, 0.8),
                DrawingPoint(0.0, 0.0),
            ),
            (
                DrawingPoint(3.2, 0.0),
                DrawingPoint(7.8, 0.0),
                DrawingPoint(7.8, 0.9),
                DrawingPoint(8.7, 0.9),
                DrawingPoint(8.7, 0.0),
                DrawingPoint(9.7, 0.0),
                DrawingPoint(9.7, 1.2),
            ),
        ),
    )


def default_fun_auth_ideas() -> tuple[FunAuthIdea, ...]:
    return (
        FunAuthIdea(
            key="sso-badge-ceremony",
            name="SSO badge ceremony",
            description=(
                "Use Microsoft, Google, or Okta SSO for the real identity check, then award "
                "a deterministic session badge after callback."
            ),
            security_role="Safe default: real auth is still OIDC, fun is presentation.",
        ),
        FunAuthIdea(
            key="conversion-lock",
            name="Conversion lock",
            description=(
                "Let users choose a number of arithmetic and base-conversion steps, then "
                "submit every intermediate result so the UI can show which steps are correct."
            ),
            security_role=(
                "Playful pre-auth puzzle; useful as a ritual before OIDC, not as identity proof."
            ),
        ),
        FunAuthIdea(
            key="operator-conversion-lock",
            name="Operator conversion lock",
            description=(
                "Show only converted numbers and ask the user for the operators that connect "
                "them, then verify by applying the submitted moves with hidden operands."
            ),
            security_role=(
                "Playful pre-auth puzzle; useful as a ritual before OIDC, not as identity proof."
            ),
        ),
        FunAuthIdea(
            key="drawing-template-gate",
            name="Drawing template gate",
            description=(
                "Ask the user to draw a simple shape, normalize the drawing to its own "
                "bounding box, and compare the form against a template."
            ),
            security_role=(
                "Playful pre-auth gate; use as experience polish before OIDC, not as identity."
            ),
        ),
        FunAuthIdea(
            key="number-guess",
            name="Number guessing game",
            description=(
                "Choose a numeric range and allowed tries. If the user misses every guess, "
                "the target resets and the failure message blames them accordingly."
            ),
            security_role="Playful pre-auth guessing gate, not an identity factor.",
        ),
        FunAuthIdea(
            key="authorized-popup",
            name="Authorized popup",
            description=(
                "Show a simple popup that says I'm authorized and pass the playful "
                "gate when the user acknowledges it."
            ),
            security_role="Cosmetic acknowledgement before the real provider auth.",
        ),
        FunAuthIdea(
            key="qr-lobby",
            name="QR lobby",
            description=(
                "Show a QR code on shared screens so users start the same OIDC login from "
                "their phone and land back in the dashboard."
            ),
            security_role="Convenience layer around provider auth.",
        ),
        FunAuthIdea(
            key="passkey-step-up",
            name="Passkey step-up",
            description=(
                "After OIDC, ask high-privilege users for a passkey challenge before showing "
                "admin-only controls."
            ),
            security_role="Real second factor when paired with WebAuthn.",
        ),
        FunAuthIdea(
            key="teams-approval",
            name="Teams approval button",
            description=(
                "For sensitive reports, send an approval card to Teams after SSO and unlock "
                "the report only after the user confirms there."
            ),
            security_role="Step-up workflow for internal tools.",
        ),
    )


def google_provider(client_id: str, client_secret: str, *, name: str = "google") -> OidcProvider:
    return OidcProvider(
        name=name,
        client_id=client_id,
        client_secret=client_secret,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    )


def microsoft_entra_provider(
    client_id: str,
    client_secret: str,
    *,
    tenant_id: str = "common",
    name: str = "microsoft",
) -> OidcProvider:
    return OidcProvider(
        name=name,
        client_id=client_id,
        client_secret=client_secret,
        server_metadata_url=(
            f"https://login.microsoftonline.com/{tenant_id}/v2.0/.well-known/openid-configuration"
        ),
    )


def _add_if_present(target: dict[str, object], key: str, value: str | None) -> None:
    if value is not None:
        target[key] = value


def _claims_from_token(token: Mapping[str, object]) -> dict[str, object]:
    for key in ("userinfo", "id_token_claims"):
        value = token.get(key)
        if isinstance(value, Mapping):
            return dict(value)
    return dict(token)


def _mission_id(mission_state: Mapping[str, object]) -> str:
    mission_id = _optional_string(mission_state.get("mission_id"))
    if mission_id is None:
        raise FunAuthDenied("Login mission state is missing a mission id.")
    return mission_id


def _prompt_keys_from_state(mission_state: Mapping[str, object]) -> tuple[str, ...]:
    raw_prompt_keys = mission_state.get("prompt_keys")
    if isinstance(raw_prompt_keys, Sequence) and not isinstance(raw_prompt_keys, str | bytes):
        prompt_keys = tuple(
            str(key) for key in raw_prompt_keys if _optional_string(key) is not None
        )
        if prompt_keys:
            return prompt_keys
    prompt_key = _optional_string(mission_state.get("prompt_key"))
    if prompt_key is None:
        raise FunAuthDenied("Login mission state is missing a prompt key.")
    return (prompt_key,)


def _prompt_index(mission_state: Mapping[str, object]) -> int:
    raw_index = mission_state.get("prompt_index", 0)
    if not isinstance(raw_index, int) or isinstance(raw_index, bool) or raw_index < 0:
        raise FunAuthDenied("Login mission state has an invalid prompt index.")
    return raw_index


def _required_operand(step: ConversionStep) -> int:
    if step.operand is None:
        raise FunAuthConfigurationError(f"Conversion step {step.kind!r} requires an operand.")
    return step.operand


def _coerce_conversion_step(step: ConversionStep | Mapping[str, object]) -> ConversionStep:
    if isinstance(step, ConversionStep):
        return step
    kind = _optional_string(step.get("kind"))
    if kind is None:
        raise FunAuthConfigurationError("Conversion step mapping must include a kind.")
    operand = step.get("operand")
    output_base = _coerce_output_base_mapping_value(step)
    label = _optional_string(step.get("label"))
    return ConversionStep(
        kind=kind,
        operand=None if operand is None else _coerce_int(operand),
        output_base=output_base,
        label=label,
    )


def _coerce_output_base_mapping_value(step: Mapping[str, object]) -> int:
    for key in ("output_base", "base", "convert_to", "conversion"):
        if key in step:
            return _coerce_output_base(step[key])
    return 10


def _coerce_output_base(value: object) -> int:
    if isinstance(value, str):
        normalized = value.strip().lower()
        aliases = {
            "binary": 2,
            "bin": 2,
            "base2": 2,
            "octal": 8,
            "oct": 8,
            "base8": 8,
            "decimal": 10,
            "dec": 10,
            "base10": 10,
            "hexadecimal": 16,
            "hex": 16,
            "base16": 16,
        }
        if normalized in aliases:
            return aliases[normalized]
    return _validate_output_base(_coerce_int(value))


def _validate_output_base(base: int) -> int:
    if base not in {2, 8, 10, 16}:
        raise FunAuthConfigurationError(f"Unsupported output base: {base}")
    return base


def _conversion_description(base: int) -> str | None:
    return {2: "binary", 8: "octal", 16: "hexadecimal"}.get(base)


def _coerce_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise FunAuthConfigurationError("Conversion values must be integers.")
    return value


def _answer_matches_number(answer: str, expected_value: int, base: int) -> bool:
    expected_text = format_number_for_base(expected_value, base)
    normalized_answer = answer.strip().lower().replace("_", "")
    normalized_expected = expected_text.lower()
    if normalized_answer == normalized_expected:
        return True

    try:
        parsed = _parse_number_answer(normalized_answer, base)
    except ValueError:
        return False
    return parsed == expected_value


def _operator_guess_tokens(value: str) -> tuple[str, ...]:
    normalized = value.strip().lower().replace(",", " ")
    if not normalized:
        return ()
    tokens = tuple(part for part in normalized.split() if part)
    if not tokens:
        return ()
    if any(any(character.isdigit() for character in token) for token in tokens):
        raise FunAuthDenied(
            "Ah, you thought it was that easy. Try again using only operators and conversions."
        )
    return tokens


def _operator_hint(step: ConversionStep) -> str:
    kind = step.kind.lower().strip()
    conversion = _conversion_description(step.resolved_output_base())
    if kind in {"add", "+"}:
        operation = "+"
    elif kind in {"subtract", "sub", "-"}:
        operation = "-"
    elif kind in {"multiply", "mul", "*"}:
        operation = "*"
    elif kind in {"integer_divide", "divide", "div", "//"}:
        operation = "//"
    elif kind in {"modulo", "mod", "%"}:
        operation = "%"
    elif kind in {"power", "pow", "**"}:
        operation = "**"
    elif kind in {"identity", "noop", "keep"}:
        operation = "keep"
    elif kind in {"to_binary", "to_bin", "binary", "bin"}:
        operation = None
        conversion = "binary"
    elif kind in {"to_hex", "hex", "hexadecimal"}:
        operation = None
        conversion = "hexadecimal"
    elif kind in {"to_octal", "to_oct", "octal", "oct"}:
        operation = None
        conversion = "octal"
    else:
        operation = step.kind

    if conversion is None:
        return operation or "keep"
    if operation is None:
        return conversion
    return f"{operation} {conversion}"


def _parse_number_answer(answer: str, base: int) -> int:
    sign = -1 if answer.startswith("-") else 1
    unsigned = answer[1:] if sign == -1 else answer
    prefixes = {2: "0b", 8: "0o", 10: "", 16: "0x"}
    if base not in prefixes:
        raise ValueError(f"Unsupported base: {base}")
    prefix = prefixes[base]
    if prefix and unsigned.startswith(prefix):
        unsigned = unsigned[len(prefix) :]
    if not unsigned:
        raise ValueError("Missing number")
    return sign * int(unsigned, base)


def _coerce_drawing_point(value: object) -> DrawingPoint:
    if isinstance(value, DrawingPoint):
        return value
    if isinstance(value, Mapping):
        return DrawingPoint(x=_coerce_number(value.get("x")), y=_coerce_number(value.get("y")))
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        if len(value) < 2:
            raise FunAuthDenied("Drawing points must include x and y coordinates.")
        return DrawingPoint(x=_coerce_number(value[0]), y=_coerce_number(value[1]))
    raise FunAuthDenied("Drawing points must be DrawingPoint objects, mappings, or x/y pairs.")


def _coerce_number(value: object) -> float:
    if not isinstance(value, Real):
        raise FunAuthDenied("Drawing coordinates must be numeric.")
    number = float(value)
    if not math.isfinite(number):
        raise FunAuthDenied("Drawing coordinates must be finite.")
    return number


def _rasterize_strokes(
    strokes: Sequence[Sequence[DrawingPoint]],
    grid_size: int,
    stroke_radius: int,
) -> set[tuple[int, int]]:
    pixels: set[tuple[int, int]] = set()
    for stroke in strokes:
        if len(stroke) == 1:
            _add_pixel_blob(pixels, stroke[0], grid_size, stroke_radius)
            continue
        for start, end in zip(stroke, stroke[1:], strict=False):
            distance = math.hypot(end.x - start.x, end.y - start.y)
            steps = max(int(math.ceil(distance * 2)), 1)
            for step in range(steps + 1):
                progress = step / steps
                point = DrawingPoint(
                    x=start.x + ((end.x - start.x) * progress),
                    y=start.y + ((end.y - start.y) * progress),
                )
                _add_pixel_blob(pixels, point, grid_size, stroke_radius)
    return pixels


def _add_pixel_blob(
    pixels: set[tuple[int, int]],
    point: DrawingPoint,
    grid_size: int,
    radius: int,
) -> None:
    center_x = round(point.x)
    center_y = round(point.y)
    for y in range(center_y - radius, center_y + radius + 1):
        for x in range(center_x - radius, center_x + radius + 1):
            is_near_point = math.hypot(x - point.x, y - point.y) <= radius + 0.5
            if 0 <= x < grid_size and 0 <= y < grid_size and is_near_point:
                pixels.add((x, y))


def _first_string(claims: Mapping[str, object], *keys: str) -> str | None:
    for key in keys:
        value = _optional_string(claims.get(key))
        if value is not None:
            return value
    return None


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_domain(domain: str) -> str:
    return domain.strip().lower().removeprefix("@")


def _domain_from_email(email: str) -> str:
    if "@" not in email:
        raise FunAuthDenied("Email address is missing a domain.")
    return email.rsplit("@", maxsplit=1)[1].lower()


def _stable_index(value: str, length: int) -> int:
    digest = hashlib.blake2b(value.encode("utf-8"), digest_size=2).digest()
    return int.from_bytes(digest, byteorder="big") % length


def _coverage(
    source: frozenset[tuple[int, int]],
    target: frozenset[tuple[int, int]],
    radius: int,
) -> float:
    if not source:
        return 0.0
    matched = 0
    for x, y in source:
        if any((x + dx, y + dy) in target for dx, dy in _neighbor_offsets(radius)):
            matched += 1
    return matched / len(source)


def _neighbor_offsets(radius: int) -> Iterable[tuple[int, int]]:
    for y in range(-radius, radius + 1):
        for x in range(-radius, radius + 1):
            if math.hypot(x, y) <= radius:
                yield x, y
