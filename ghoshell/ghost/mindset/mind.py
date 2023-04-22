from __future__ import annotations

from abc import ABCMeta, abstractmethod
from typing import Optional, Any, List

from ghoshell.ghost.mindset.thought import Thought
from ghoshell.ghost.operator import Operator
from ghoshell.ghost.runtime import TASK_LEVEL
from ghoshell.ghost.url import URL


class Mind(metaclass=ABCMeta):

    # ---- 思维重定向的命令 ---- #

    def __init__(self, this: Thought):
        this.attentions = None
        self.this = this

    def level(self, level: TASK_LEVEL) -> Mind:
        self.this.level = level
        return self

    def overdue(self, overdue: int) -> Mind:
        self.this.overdue = overdue
        return self

    def priority(self, priority: float) -> Mind:
        self.this.priority = priority
        return self

    @abstractmethod
    def go_stage(self, stage: str) -> "Operator":
        pass

    @abstractmethod
    def redirect_to(self, to: "URL") -> "Operator":
        """
        从当前任务, 进入一个目标任务.
        自己会根据实际状态, 被系统调度或垃圾回收.
        """
        pass

    # ---- 中断命令 ---- #

    def awaits(self, focus_stages: List[str] = None, focus_thinks: List[URL] = None) -> "Operator":
        """
        本来想用 await, 无奈 python 的系统关键字太多, 这是 python 一个巨大的缺点.
        wait 是挂起整个 Clone. 上下文也会同步休眠, 等待下一次 input 的唤醒.

        而实际上, 当前 Process 进入了 wait 状态, 可能 clone 还不会立刻释放 (unlock), 而是继续去处理异步消息.
        就看具体怎么实现了.
        """
        return self._focus_stages(focus_stages)._focus_thinks(focus_thinks)._do_await()

    @abstractmethod
    def _do_await(self) -> "Operator":
        pass

    def _focus_stages(self, stages: List[str] | None) -> Mind:
        if not stages:
            return self
        attentions = self.this.attentions
        if attentions is None:
            attentions = []
        self_url = self.this.url
        for stage in stages:
            attentions.append(self_url.to_stage(stage))
        self.this.attentions = attentions
        return self

    def _focus_thinks(self, thinks: List[URL] | None) -> Mind:
        if not thinks:
            return self
        attentions = self.this.attentions
        if attentions is None:
            attentions = []
        for url in thinks:
            url.stage = ""
            attentions.append(url)
        self.this.attentions = attentions
        return self

    @abstractmethod
    def depend_on(self, target: "URL") -> "Operator":
        """
        依赖一个目标任务, 目标任务完成后会发起回调.
        这个目标任务也可能在运行中, depend_on 不会去指定任何 stage.
        每个 Think 对于别的 Think 而言内部是封闭的.
        """
        pass

    # ---- 任务内部命令. ---- #

    @abstractmethod
    def repeat(self) -> "Operator":
        """
        重复上一轮交互的终点状态, 触发 OnRepeat 事件.
        Repeat 不必重复上一轮交互的所有输出, 只需要 Repeat 必要的输出.
        这个命令对于对话机器人比较有用, 比如机器人向用户询问了一个问题
        执行 Repeat 就会重复去问用户.

        用 LLM 可以将 Repeat 事件直接告知 LLM, 让它自行重复.
        """
        pass

    @abstractmethod
    def restart(self) -> "Operator":
        """
        重启当前的 Task. 与 go_stage('') 不同, 还会重置掉上下文状态 (重置 thought)
        """
        pass

    # ---- 全局命令 ---- #

    @abstractmethod
    def rewind(self, repeat: bool = False) -> "Operator":
        """
        重置当前对话状态. 忽视本轮交互的变更.
        如果执行了 rewind, 理论上不会保存当前交互产生出来的 Process/Task 等变更
        而是当作什么都没发生过.
        如果要做更复杂的实现, 就不用 rewind 方法了.

        以前的 commune chatbot 不仅实现了 rewind, 还实现了 process snapshots
        可以通过 backward 指令返回 n 轮对话之前.
        这种 rollback 的能力极其复杂, 实际上没有任何办法完美实现.
        因为在思考运行的过程中, 必然有 IO 已经发生了.
        """
        pass

    @abstractmethod
    def reset(self) -> "Operator":
        """
        Reset 的对象是整个会话的 Process, 会清空所有任务回到起点.
        通常用于兼容一些低水平的异常. 出故障后重置状态
        对于不可恢复的异常, 也要有一整套恢复办法.

        典型的例子是 task 数据结构变化, 导致记忆回复时会产生 RuntimeException
        或者 mindset 做了无法向前兼容的改动, 导致 runtime 记忆出错.
        """
        pass

    @abstractmethod
    def quit(self, reason: Optional[Any] = None) -> "Operator":
        """
        退出整个进程.
        会从 current_task 开始逐个 cancel, 一直 cancel 到 root
        这也意味着 cancel 的过程中可以中断.
        """
        pass