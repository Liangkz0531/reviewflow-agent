from collections.abc import Iterable


def average(values: Iterable[float]) -> float:
    numbers = list(values)
    if not numbers:
        raise ValueError("values cannot be empty")
    return sum(numbers) / len(numbers)
