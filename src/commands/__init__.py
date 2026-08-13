"""command book 層：把動作抽象成可組合的指令，並提供攻擊決策。"""
from src.commands.command_book import (
    Command,
    CommandContext,
    CommandBook,
    MoveCommand,
    AttackCommand,
    BuffCommand,
    PotionCommand,
    ChangeChannelCommand,
    ApproachCommand,
    COMMAND_REGISTRY,
)
from src.commands.combat import (
    CombatDecision,
    select_nearest,
    dominant_side,
    plan_attack,
    plan_ranged,
    decision_to_actions,
)

__all__ = [
    "Command",
    "CommandContext",
    "CommandBook",
    "MoveCommand",
    "AttackCommand",
    "BuffCommand",
    "PotionCommand",
    "ChangeChannelCommand",
    "ApproachCommand",
    "COMMAND_REGISTRY",
    "CombatDecision",
    "select_nearest",
    "dominant_side",
    "plan_attack",
    "plan_ranged",
    "decision_to_actions",
]
