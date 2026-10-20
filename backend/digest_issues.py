"""Правило утренней сводки: в алерт только незакрытые сбои."""


def still_failing(last_error_at, last_success_at) -> bool:
    """Сбой актуален, если ошибки ещё не закрыл более поздний успех."""
    if last_error_at is None:
        return False
    if last_success_at is None:
        return True
    return last_error_at > last_success_at
