"""
Глобальный флаг остановки — кнопка «Стоп» в лаунчере. run_task (шаги по экрану) и
orchestrate (диспетчер) проверяют его между шагами и прекращают работу. Это мягкая
остановка: текущее действие доигрывается, новые не начинаются. Мгновенный стоп —
перехват курсора (защита RealEnv).
"""
import threading

_stop = threading.Event()


def request_stop():
    _stop.set()


def clear():
    _stop.clear()


def stopped() -> bool:
    return _stop.is_set()
