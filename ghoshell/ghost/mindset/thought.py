from __future__ import annotations

from abc import ABCMeta, abstractmethod
from typing import Dict, List

from ghoshell.ghost.runtime import TASK_LEVEL, TaskLevel
from ghoshell.ghost.url import URL


class Thought(metaclass=ABCMeta):
    """
    当前任务的状态.
    可以理解成一个函数的运行栈
    args 是入参
    vars 则是运行中的变量.

    这个 This 需要每个 Think 能力自定义一个协议, 主要是 variables 需要一个协议.

    thought 的生命周期: task => thought => task
    thought 是 task 与 mindset 互动时的中间态数据, 用来做强类型提示.
    """
    tid: str

    # 入参数据
    url: URL

    overdue: int

    priority: float

    level: TASK_LEVEL

    attentions: List[URL] = None

    def __init__(
            self,
            tid: str,
            url: URL,
            overdue: int = 0,
            priority: float = 0,
            level: TASK_LEVEL = TaskLevel.LEVEL_PUBLIC,
    ):
        self.tid = tid
        self.url = url.copy()
        self.overdue = overdue
        self.priority = priority
        self.level = level
        self.prepare(url.args)

    # ---- 抽象方法 ---- #
    @abstractmethod
    def prepare(self, args: Dict) -> None:
        """
        初始化
        """
        pass

    @abstractmethod
    def set_variables(self, variables: Dict) -> None:
        """
        设置上下文数据, 通常是一个 dict, 可以用 BaseModel 转成协议.
        """
        pass

    @abstractmethod
    def vars(self) -> Dict | None:
        """
        返回当前上下文中的变量.
        """
        pass

    @abstractmethod
    def destroy(self) -> None:
        pass