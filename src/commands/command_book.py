"""command book：動作指令定義與註冊表。

每個指令是一個可被 routine 呼叫的原子動作（移動、攻擊、buff、補水、換頻…）。
新增動作只需繼承 Command 並在 COMMAND_REGISTRY / CommandBook 註冊，
engine 與 routine 都無需改動——這是三層解耦的關鍵擴充點。
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Dict, Type


@dataclass
class CommandContext:
    """指令執行時可取用的共用資源。

    屬性：
        controller: InputController，負責送出按鍵。
        vision: dict，包含 matcher / minimap / health 與當前 frame。
        config: 已載入的設定 dict。
        state: 跨指令共享的可變狀態（例如角色面向、目前定位點）。
    """
    controller: Any
    vision: Dict[str, Any]
    config: Dict[str, Any]
    state: Dict[str, Any] = field(default_factory=dict)


class Command(abc.ABC):
    """所有動作指令的基底類別。"""

    name: str = "command"

    def __init__(self, **args: Any) -> None:
        self.args = args

    @abc.abstractmethod
    def execute(self, ctx: "CommandContext") -> None:
        """執行動作。子類別必須實作。"""
        raise NotImplementedError

    def __repr__(self):
        return f"<{self.__class__.__name__} {self.args}>"


class MoveCommand(Command):
    """移動到指定路線點。"""
    name = "move"

    def execute(self, ctx):
        # TODO: 讀取小地圖角色座標，與目標點比較，決定往左/右移動與跳躍。
        #       目前僅示意：記錄目標點，實際走位待實作。
        ctx.state["moving_to"] = self.args.get("to")
        # 範例（待實作）：
        # keys = ctx.config.get("keys", {})
        # ctx.controller.hold(keys.get("move_right"), 0.3)


class AttackCommand(Command):
    """朝指定方向攻擊數次。"""
    name = "attack"

    def execute(self, ctx):
        keys = ctx.config.get("keys", {})
        facing = self.args.get("facing", "right")
        repeat = int(self.args.get("repeat", 1))
        # 攻擊技能鍵：優先用 combat.attack_key（弓箭手放箭），否則退回 keys.attack
        attack_key = ctx.config.get("combat", {}).get("attack_key") or keys.get("attack", "ctrl")
        # 先轉向面對怪物，再放技能連射
        turn_key = keys.get("move_left" if facing == "left" else "move_right")
        if turn_key:
            ctx.controller.tap(turn_key)
        for _ in range(repeat):
            ctx.controller.tap(attack_key)


class BuffCommand(Command):
    """依序施放所有 buff 技能。"""
    name = "buff"

    def execute(self, ctx):
        keys = ctx.config.get("keys", {})
        for key in keys.get("buff_skills", []) or []:
            ctx.controller.tap(key)


class PotionCommand(Command):
    """補血或補魔（kind = 'hp' | 'mp'）。"""
    name = "potion"

    def execute(self, ctx):
        keys = ctx.config.get("keys", {})
        kind = self.args.get("kind", "hp")
        key = keys.get("hp_potion" if kind == "hp" else "mp_potion")
        if key:
            ctx.controller.tap(key)


class ChangeChannelCommand(Command):
    """換頻（例如有其他玩家佔點時）。"""
    name = "change_channel"

    def execute(self, ctx):
        # TODO: 開啟換頻選單 → 選擇頻道 → 確認。此處僅送出換頻鍵示意。
        keys = ctx.config.get("keys", {})
        key = keys.get("change_channel")
        if key:
            ctx.controller.tap(key)


class ApproachCommand(Command):
    """朝指定方向走近一步（打怪時用來靠近尚未進入攻擊距離的目標）。"""
    name = "approach"

    def execute(self, ctx):
        keys = ctx.config.get("keys", {})
        direction = self.args.get("dir", "right")
        key = keys.get("move_left" if direction == "left" else "move_right")
        if key:
            ctx.controller.hold(key, float(self.args.get("seconds", 0.2)))


class JumpMoveCommand(Command):
    """朝指定方向邊走邊跳（換平台用：站到平台下方後跳上去）。

    按住方向鍵 → 按跳躍 → 方向鍵續按 carry_seconds 讓跳躍帶水平位移。
    """
    name = "jump_move"

    def execute(self, ctx):
        import time
        keys = ctx.config.get("keys", {})
        direction = self.args.get("dir")
        jump_key = keys.get("jump", "alt")
        dir_key = (keys.get("move_left" if direction == "left" else "move_right")
                   if direction else None)
        if dir_key:
            ctx.controller.key_down(dir_key)
        try:
            ctx.controller.tap(jump_key)
            if dir_key:
                time.sleep(float(self.args.get("carry_seconds", 0.45)))
        finally:
            if dir_key:
                ctx.controller.key_up(dir_key)


COMMAND_REGISTRY: Dict[str, Type[Command]] = {
    cls.name: cls
    for cls in (MoveCommand, AttackCommand, BuffCommand, PotionCommand,
                ChangeChannelCommand, ApproachCommand, JumpMoveCommand)
}


class CommandBook:
    """動作指令註冊表 / 工廠。

    用法：
        >>> book = CommandBook()
        >>> cmd = book.create("attack", {"facing": "left", "repeat": 2})
        >>> cmd.execute(ctx)
    """

    def __init__(self, registry=None):
        self.registry: Dict[str, Type[Command]] = dict(registry or COMMAND_REGISTRY)

    def create(self, action, args=None):
        """依動作名稱建立指令實例。"""
        cls = self.registry.get(action)
        if cls is None:
            raise KeyError(f"未知的動作：{action!r}（可用：{sorted(self.registry)}）")
        return cls(**(args or {}))

    def register(self, cls):
        """註冊自訂指令類別（擴充點）。"""
        self.registry[cls.name] = cls
        return self

    @property
    def actions(self):
        return sorted(self.registry.keys())
