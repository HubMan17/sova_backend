# -*- coding: utf-8 -*-
from __future__ import annotations
from dataclasses import dataclass

# Глобальные лимиты (можешь подтянуть из settings/env)
ARM_COUNT_LIMIT = 10           # кол-во ARM
ARM_TIME_LIMIT_S = 5*60 + 50   # 5м 50с в секундах
QSTAB_TIME_LIMIT_S = 30        # 30с



NBSP = "\u00A0"

# Пороговые уровни (в процентах)
THRESHOLDS = (50, 75, 90, 100)


def fmt_hms(seconds: float | int) -> str:
    s = int(max(0, round(float(seconds or 0))))
    h, m = divmod(s, 3600)
    m, s = divmod(m, 60)
    if h:  return f"{h}ч {m}м {s}с"
    if m:  return f"{m}м {s}с"
    return f"{s}с"


def pct(value: float, limit: float) -> int:
    if not limit:
        return 0
    return max(0, min(100, int(round((value / limit) * 100))))


def reached_levels(p: int) -> list[int]:
    """Какие пороги достигнуты данным процентом."""
    return [t for t in THRESHOLDS if p >= t]


@dataclass
class ArmProgress:
    arms: int          # использовано ARM (шт)
    arm_sec: float     # время под ARM (сек)
    qstab_sec: float   # время в QSTAB (сек)

    # лимиты (могут быть переопределены снаружи, при желании)
    limit_arms: int = 10
    limit_arm_sec: float = 350.0   # 5м 50с
    limit_qstab_sec: float = 30.0

    @property
    def count_pct(self) -> float:
        return 0.0 if self.limit_arms <= 0 else min(100.0, 100.0 * self.arms / self.limit_arms)

    @property
    def time_pct(self) -> float:
        return 0.0 if self.limit_arm_sec <= 0 else min(100.0, 100.0 * self.arm_sec / self.limit_arm_sec)

    @property
    def qstab_pct(self) -> float:
        return 0.0 if self.limit_qstab_sec <= 0 else min(100.0, 100.0 * self.qstab_sec / self.limit_qstab_sec)


def _fmt_dur(sec: float | int) -> str:
    s = max(0, int(round(float(sec or 0))))
    m, ss = divmod(s, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}ч{NBSP}{m}м{NBSP}{ss}с"
    if m:
        return f"{m}м{NBSP}{ss}с"
    return f"{ss}с"


def _left(used: float, limit_: float) -> int:
    return max(0, int(round((limit_ or 0) - (used or 0))))


def _current_threshold(pct: float) -> int | None:
    """возвращает ТЕКУЩИЙ достигнутый порог (максимальный из THRESHOLDS, не выше pct), либо None"""
    for t in reversed(THRESHOLDS):
        if pct >= t - 1e-9:
            return t
    return None


def build_arm_report_message(
    *,
    ts_str: str,          # "HH:MM:SS dd.mm.yyyy"
    board_label: str,     # "#133"
    progress: ArmProgress
) -> str:
    pr = progress

    # вычисления
    count_pct = int(round(pr.count_pct))
    time_pct = int(round(pr.time_pct))
    qstab_pct = int(round(pr.qstab_pct))

    left_count = max(0, pr.limit_arms - pr.arms)
    left_arm_sec = _left(pr.arm_sec, pr.limit_arm_sec)
    left_qstab_sec = _left(pr.qstab_sec, pr.limit_qstab_sec)

    count_pair = f"{pr.arms}{NBSP}/{NBSP}{pr.limit_arms}"
    arm_pair = f"{_fmt_dur(pr.arm_sec)}{NBSP}/{NBSP}{_fmt_dur(pr.limit_arm_sec)}"
    qstab_pair = f"{_fmt_dur(pr.qstab_sec)}{NBSP}/{NBSP}{_fmt_dur(pr.limit_qstab_sec)}"

    # итоговая строка-нарушения
    violations: list[str] = []
    if pr.arms >= pr.limit_arms:
        violations.append(f"достигнут лимит по ARM-счётчику ({count_pair})")
    if pr.arm_sec >= pr.limit_arm_sec:
        violations.append(f"достигнуто ARM-время ({arm_pair})")
    if pr.qstab_sec >= pr.limit_qstab_sec:
        violations.append(f"достигнуто QSTAB-время ({qstab_pair})")

    viol_block = "\n".join(f"• {v}" for v in violations) if violations else "— Нет"

    # ТЕКУЩИЕ достигнутые пороги (не список всех, а только актуальный)
    th_count = _current_threshold(count_pct)
    th_time = _current_threshold(time_pct)
    th_qstab = _current_threshold(qstab_pct)

    th_count_line = f"• Порог ARM-счётчика достигнут: {th_count}%" if th_count else "• Порог ARM-счётчика достигнут: —"
    th_time_line = f"• Порог ARM-времени достигнут: {th_time}%" if th_time else "• Порог ARM-времени достигнут: —"
    th_qstab_line = f"• Порог QSTAB-времени достигнут: {th_qstab}%" if th_qstab else "• Порог QSTAB-времени достигнут: —"

    # сообщение
    return (
        "🛠 <b>Технический отчёт</b>\n"
        f"⏱️ <b>Время:</b> {ts_str}\n"
        f"📟 <b>Борт:</b> {board_label}\n\n"
        "🟥 <b>Нарушения:</b>\n"
        f"{viol_block}\n\n"
        "🟩 <b>Параметры:</b>\n"
        f"• Кол-во ARM: <b>{count_pct}%</b> ({count_pair}), запас: {left_count} шт\n"
        f"• Время под ARM: <b>{time_pct}%</b> ({arm_pair}), запас: {_fmt_dur(left_arm_sec)}\n"
        f"• Время в QSTAB: <b>{qstab_pct}%</b> ({qstab_pair}), запас: {_fmt_dur(left_qstab_sec)}\n\n"
        "<b>Пороги:</b>\n"
        f"{th_count_line}\n"
        f"{th_time_line}\n"
        f"{th_qstab_line}"
    )