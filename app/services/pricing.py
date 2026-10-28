from app.config import (
    CACHED_INPUT_PER_1K_MICROS,
    CALL_PRICE_MICROS,
    FRESH_INPUT_PER_1K_MICROS,
    MICROS_PER_DOLLAR,
    OUTPUT_PER_1K_MICROS,
)


def ceil_div(a: int, b: int) -> int:
    """Integer division that rounds UP, without floats. Works for a >= 0 and b > 0."""
    return (a + b - 1) // b


def calculate_cost(
    calls: int, fresh: int, cached: int, output: int, reasoning: int
) -> dict:
    """Turn usage totals into money (whole micro-dollars).

    Pass the MONTHLY TOTALS, so the rounding happens only once per bucket.
    """
    if min(calls, fresh, cached, output, reasoning) < 0:
        raise ValueError("usage numbers cannot be negative")

    calls_micros = calls * CALL_PRICE_MICROS
    fresh_micros = ceil_div(fresh * FRESH_INPUT_PER_1K_MICROS, 1000)
    cached_micros = ceil_div(cached * CACHED_INPUT_PER_1K_MICROS, 1000)
    # Reasoning tokens are billed as output tokens, so they join the output bucket.
    output_micros = ceil_div((output + reasoning) * OUTPUT_PER_1K_MICROS, 1000)

    return {
        "calls_micros": calls_micros,
        "fresh_input_micros": fresh_micros,
        "cached_input_micros": cached_micros,
        "output_and_reasoning_micros": output_micros,
        "usage_micros": calls_micros + fresh_micros + cached_micros + output_micros,
    }


def format_usd(micros: int) -> str:
    """Show micro-dollars as a dollar text, using integers only (3475 -> '0.003475')."""
    dollars, rest = divmod(micros, MICROS_PER_DOLLAR)
    return f"{dollars}.{rest:06d}"
