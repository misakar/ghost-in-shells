from abc import ABCMeta, abstractmethod
from typing import Optional, List, ClassVar, Type, Dict

from ghoshell.ghost import Context, URL
from ghoshell.ghost import CtxTool
from ghoshell.ghost import Intending, Receiving, Activating, Preempting, Callback
from ghoshell.ghost import Operator
from ghoshell.ghost import RuntimeTool
from ghoshell.ghost import Task, TaskStatus, TaskLevel
from ghoshell.ghost import Withdrawing, Canceling, Failing, Quiting
from ghoshell.messages import Tasked


class AbsOperator(Operator, metaclass=ABCMeta):
    """
    operator 基类. 没有提供有用的方法, 只是提供一个开发范式
    方便开发者建立思路, 划分边界.
    """

    def run(self, ctx: "Context") -> Optional["Operator"]:
        """
        用一个标准流程来约定 Operator 的开发方式.
        """
        # 先看是否有拦截发生, 如果发生了拦截, 则 operator 不会真正执行.
        interceptor = self._intercept(ctx)
        if interceptor is not None:
            return interceptor
        # 运行 operator 的事件.
        result_op = self._run_operation(ctx)
        if result_op is not None:
            return result_op
        # 如果运行事件没结果, 就往后走.
        return self._fallback(ctx)

    @abstractmethod
    def _intercept(self, ctx: "Context") -> Optional["Operator"]:
        """
        判断是否有拦截事件, 可以组织 operator 运行
        """
        pass

    @abstractmethod
    def _run_operation(self, ctx: "Context") -> Optional["Operator"]:
        """
        触发 Operator 自身的事件.
        """
        pass

    @abstractmethod
    def _fallback(self, ctx: "Context") -> Optional["Operator"]:
        """
        如果没有任何中断, 则继续往后运行.
        """
        pass


class ChainOperator(Operator):
    """
    链式 operator
    """

    def __init__(self, chain: List[Operator]):
        self.chain = chain

    def run(self, ctx: "Context") -> Optional["Operator"]:
        chain = self.chain
        if len(chain) == 0:
            return None
        op = chain[0]
        chain = chain[1:]
        after = op.run(ctx)
        if after is None:
            return ChainOperator(chain)
        if len(chain) == 0:
            return after
        chain.insert(0, after)
        return ChainOperator(chain)

    def destroy(self) -> None:
        del self.chain


class ReceiveInputOperator(AbsOperator):
    """
    接受到一个 Input
    """

    def _intercept(self, ctx: "Context") -> Optional["Operator"]:
        process = ctx.clone.runtime.current_process()
        tasked = ctx.read(Tasked)

        # root 如果没有初始化, 需要执行初始化根节点.
        if not process.root:
            if tasked is not None:
                root = RuntimeTool.new_task(
                    ctx,
                    URL.new(resolver=tasked.resolver, stage="", args=tasked.args),
                )
            else:
                # 否则 root 用默认方式生成.
                root = RuntimeTool.new_task(ctx, ctx.clone.root)
            RuntimeTool.store_task(ctx, root)
            # 保存变更. 这一步理论上不是必须的.
            ctx.clone.runtime.store_process(process)

        if tasked is not None:
            # tasked 的情况, 只需要执行 tasked 的任务就可以了.
            # 这是一种特殊的消息, 通常是内部的消息.
            return OnMessageTaskedOperator(tasked)

        # 正常情况下, 要判断是不是 new
        # 如果是 new 的话, 要初始化根节点.
        if process.is_new:
            # 保证不再是 new 了.
            process.add_round()
            ctx.clone.runtime.store_process(process)
            root_task = RuntimeTool.fetch_root_task(ctx)
            # 必须先激活根节点, 然后让它进入某个状态后, 开始 receive input.
            # todo: 激活的过程是否要
            return ChainOperator([ActivateOperator(root_task.url, None, root_task.tid), ReceiveInputOperator()])
        return None

    def _run_operation(self, ctx: "Context") -> Optional["Operator"]:

        # 如果 payload tid 存在, 则消息希望命中目标任务. 需要调整任务的优先顺序.
        self._check_payload_tid(ctx)

        awaiting_task = RuntimeTool.fetch_awaiting_task(ctx)

        # 目前认为需要分批匹配意图. 第一批是前序意图, 决定重定向方向.
        intentions = CtxTool.context_forward_intentions(ctx, awaiting_task.level)
        matched = CtxTool.match_intentions(ctx, intentions)
        if matched is not None:
            return IntendingOperator(matched.to, matched.fr, matched.matched)

        # 第二批是后续意图, 用来激活后续任务
        intentions = CtxTool.context_backward_intentions(ctx, awaiting_task.level)
        matched = CtxTool.match_intentions(ctx, intentions)
        if matched is not None:
            return IntendingOperator(matched.to, matched.fr, matched.matched)

        # 都没有匹配, 就尝试模糊匹配.
        if awaiting_task.level == TaskLevel.LEVEL_PUBLIC:
            matched = CtxTool.match_global_intentions(ctx)
            if matched is not None:
                return IntendingOperator(matched.to, matched.fr, matched.matched)

        #  所有意图匹配逻辑都没有命中, 往后走.
        return None

    def _fallback(self, ctx: "Context") -> Optional["Operator"]:
        return UnhandledInputOperator()

    @classmethod
    def _check_payload_tid(cls, ctx: Context) -> None:
        """
        如果 payload.tid 存在, 调整任务的优先顺序.
        """
        payload = ctx.input.payload
        if not payload.tid:
            return
        target_task = RuntimeTool.fetch_task(ctx, payload.tid)
        if target_task is None:
            return
        if target_task.status != TaskStatus.WAITING:
            return
        process = ctx.clone.runtime.current_process()
        process.await_at(target_task.tid)
        ctx.clone.runtime.store_process(process)
        return

    def destroy(self) -> None:
        return


class IntendingOperator(AbsOperator):
    """
    匹配到了意图并且执行跳转.
    """

    def __init__(self, to: URL, fr: URL | None, params: Dict | None):
        self.to = to
        self.fr = fr
        self.params = params

    def _intercept(self, ctx: "Context") -> Optional["Operator"]:
        return None

    def _run_operation(self, ctx: "Context") -> Optional["Operator"]:
        if self.to.stage:
            return self._intend_to_stage(ctx)
        return self._match_think_intention(ctx)

    def _intend_to_stage(self, ctx: "Context") -> Optional["Operator"]:
        task = RuntimeTool.fetch_task_by_url(ctx, self.to, False)
        # 只有任务存在的时候, 才能触发命中意图的任务.
        # 否则不允许命中这种意图.
        if task is not None:
            event = Intending(task.tid, self.to.stage, self.fr, self.params)
            return RuntimeTool.fire_event(ctx, event)
        return None

    def _match_think_intention(self, ctx: "Context") -> Optional["Operator"]:
        url = self.to
        url.stage = ""
        if self.params:
            url = url.new_with(args=self.params)

        task = RuntimeTool.fetch_task_by_url(ctx, self.to, False)
        # 任务没有初始化过. 使用 matched 作为参数, 进入任务.
        if task is None:
            # 启动目标任务.
            return ActivateOperator(url.new_with(args=self.params), self.fr, None)

        # 任务已经初始化过. 允许命中初始节点.
        event = Intending(task.tid, url.stage, self.fr, self.params)
        # 命中了都应该要返回, 无法处理才返回 None
        return RuntimeTool.fire_event(ctx, event)

    def _fallback(self, ctx: "Context") -> Optional["Operator"]:
        return UnhandledInputOperator()

    def destroy(self) -> None:
        del self.fr
        del self.to
        del self.params


#
# class RedirectOperator(AbsOperator):
#
#     def __init__(self, target: URL, fr: URL | None):
#         self.target = target
#         self.fr = fr
#
#     def _intercept(self, ctx: "Context") -> Optional["Operator"]:
#         # todo: 未来要做拦截?
#         return None
#
#     def _run_operation(self, ctx: "Context") -> Optional["Operator"]:
#         task = RuntimeTool.fetch_task_by_url(ctx, self.target, True)
#         task.status = TaskStatus.RUNNING
#         RuntimeTool.store_task(ctx, task)
#         return ActivateOperator(self.target, self.fr)
#
#     def _fallback(self, ctx: "Context") -> Optional["Operator"]:
#         return None
#
#     def destroy(self) -> None:
#         del self.target
#         del self.fr


class OnMessageTaskedOperator(AbsOperator):
    """
    响应以传输的任务数据为消息的请求.
    """

    def __init__(self, tasked: Tasked):
        self.tasked = tasked

    def _intercept(self, ctx: "Context") -> Optional["Operator"]:
        return None

    def _run_operation(self, ctx: "Context") -> Optional["Operator"]:
        tasked = self.tasked
        url = URL(think=tasked.resolver, stage=tasked.stage, args=tasked.args.copy())
        task = RuntimeTool.fetch_task_by_url(ctx, url, True)
        # 保存任务的状态
        task.merge_tasked(tasked)
        RuntimeTool.store_task(ctx, task)
        # 进入到下一个状态.
        match task.status:
            case TaskStatus.DEAD:
                return CancelOperator(task.tid, None)
            case TaskStatus.WAITING:
                return AwaitOperator(task.tid, None)
            case TaskStatus.FINISHED:
                return FinishOperator(task.tid, task.url.stage)
            case _:
                return ScheduleOperator()

    def _fallback(self, ctx: "Context") -> Optional["Operator"]:
        # 什么也不干.
        return RewindOperator()

    def destroy(self) -> None:
        del self.tasked


class ActivateOperator(AbsOperator):

    def __init__(self, to: URL, fr: URL | None, tid: str | None):
        self.to = to
        self.fr = fr
        self.tid = tid

    def _intercept(self, ctx: "Context") -> Optional["Operator"]:
        return None

    def _run_operation(self, ctx: "Context") -> Optional["Operator"]:
        if self.tid:
            task = RuntimeTool.force_fetch_task(ctx, self.tid)
        else:
            task = RuntimeTool.fetch_task_by_url(ctx, self.to, True)
        match task.status:
            case TaskStatus.NEW:
                think = CtxTool.force_fetch_think(ctx, self.to.resolver)
                if think.is_async():
                    # 设置 task 为 yielding, 保留了一个指针.
                    task.status = TaskStatus.YIELDING
                    RuntimeTool.store_task(ctx, task)
                    # 发送异步消息, 新开一个子进程.
                    ctx.async_input(task.to_tasked(), None)
                    # 正常回调任务, 当前任务已经 yielding.
                    return ScheduleOperator()
                # 保证至少是 Running 状态.
                task.status = TaskStatus.RUNNING
                RuntimeTool.store_task(ctx, task)

                event = Activating(task.tid, self.to.stage, self.fr)
                return RuntimeTool.fire_event(ctx, event)
            case TaskStatus.FINISHED, TaskStatus.DEAD:
                # 重启任务.
                task.restart()
                RuntimeTool.store_task(ctx, task)
                event = Activating(task.tid, self.to.stage, self.fr)
                return RuntimeTool.fire_event(ctx, event)

            # preempting
            case TaskStatus.PREEMPTING, TaskStatus.DEPENDING, TaskStatus.YIELDING:
                event = Preempting(task.tid, task.url.stage, self.fr)
                return RuntimeTool.fire_event(ctx, event)
            case _:
                event = Activating(task.tid, self.to.stage, self.fr)
                return RuntimeTool.fire_event(ctx, event)

    def _fallback(self, ctx: "Context") -> Optional["Operator"]:
        # 启动了目标节点, 但没有发生任何事件?
        return None

    def destroy(self) -> None:
        del self.to
        del self.fr
        del self.tid


class RewindOperator(AbsOperator):

    def __init__(self, repeat: bool = False):
        self.repeat = repeat

    def _intercept(self, ctx: "Context") -> Optional["Operator"]:
        return None

    def _run_operation(self, ctx: "Context") -> Optional["Operator"]:
        ctx.clone.runtime.rewind()
        if self.repeat:
            return AwaitOperator(None, None)
        return None

    def _fallback(self, ctx: "Context") -> Optional["Operator"]:
        return None

    def destroy(self) -> None:
        del self.repeat


class AwaitOperator(AbsOperator):

    def __init__(self, tid: str | None, stage: str | None):
        self.tid = tid
        self.stage = stage

    def _intercept(self, ctx: "Context") -> Optional["Operator"]:
        return None

    def _run_operation(self, ctx: "Context") -> Optional["Operator"]:
        tid = self.tid
        runtime = ctx.clone.runtime
        process = runtime.current_process()
        # 变更 awaiting 任务.
        if tid is not None:
            process.awaiting = tid
        runtime.store_process(process)

        task = RuntimeTool.fetch_task(ctx, tid)
        # 变更当前状态.
        if self.stage is not None:
            task.url.stage = self.stage

        stage = CtxTool.force_fetch_stage(ctx, task.url.resolver, task.url.stage)
        attentions = stage.on_await(ctx)
        for url in attentions:
            self_think = task.url.resolver
            # 不允许调用别的任务的子状态. 在这里主动清空掉.
            # 类似面向对象, 只能调用入口代码.
            if url.stage != "" and url.resolver != self_think:
                url.stage = ""

        task.attentions = attentions
        RuntimeTool.store_task(ctx, task)
        # 任务结束.
        return None

    def _fallback(self, ctx: "Context") -> Optional["Operator"]:
        return None

    def destroy(self) -> None:
        del self.tid


class ForwardOperator(AbsOperator):
    """
    让当前任务向前运行.
    """

    def __init__(self, tid: str, stages: List[str]):
        self.tid = tid
        self.stages = stages

    def _intercept(self, ctx: "Context") -> Optional["Operator"]:
        return None

    def _run_operation(self, ctx: "Context") -> Optional["Operator"]:
        # 状态变更.
        task = RuntimeTool.fetch_task(ctx, self.tid)
        if len(self.stages) > 0:
            task.insert(self.stages)
            RuntimeTool.store_task(ctx, task)

        return self._forward(ctx)

    def _forward(self, ctx: "Context") -> Optional["Operator"]:
        task = RuntimeTool.fetch_task(ctx, self.tid)
        _next = task.forward()
        if _next is not None:
            # 启动目标节点.
            return ActivateOperator(task.url.new_with(stage=_next), task.url, task.tid)
        # 结束当前 task, 就在当前位置.
        return FinishOperator(task.tid, task.url.stage)

    def _fallback(self, ctx: "Context") -> Optional["Operator"]:
        return None

    def destroy(self) -> None:
        del self.tid
        del self.stages


class FinishOperator(AbsOperator):
    def __init__(self, tid: str, stage: str):
        self.tid = tid
        self.stage = stage

    def _intercept(self, ctx: "Context") -> Optional["Operator"]:
        return None

    def _run_operation(self, ctx: "Context") -> Optional["Operator"]:
        # 变更状态
        task = RuntimeTool.fetch_task(ctx, self.tid)
        # 更新状态.
        callbacks = task.done(TaskStatus.FINISHED, self.stage)
        # 没有回调节点.
        if not callbacks:
            return None

        tasks = RuntimeTool.fetch_process_tasks_by_ids(ctx, list(callbacks))
        # 遍历所有依赖当前任务的那些任务.
        preempting = []
        for ptr in tasks:
            # depending 任务调整为 blocking 任务
            ptr.status = TaskStatus.PREEMPTING
            preempting.append(ptr)

        # 只保存 runtime 变更, 不涉及 data.
        RuntimeTool.store_task(ctx, *preempting)
        return None

    def _fallback(self, ctx: "Context") -> Optional["Operator"]:
        return ScheduleOperator()

    def destroy(self) -> None:
        del self.tid
        del self.stage


class UnhandledInputOperator(AbsOperator):

    def _intercept(self, ctx: "Context") -> Optional["Operator"]:
        pass

    def _run_operation(self, ctx: "Context") -> Optional["Operator"]:
        awaiting_task = RuntimeTool.fetch_awaiting_task(ctx)
        #  让 current 对话任务来做兜底
        after = self._fallback_to_task(ctx, awaiting_task)
        if after is not None:
            return after
        # 让 root 级别的对话任务来做兜底.
        root_task = RuntimeTool.fetch_root_task(ctx)
        if root_task.tid == awaiting_task.tid:
            return None

        after = self._fallback_to_task(ctx, root_task)
        if after is not None:
            return after
        return None

    @classmethod
    def _fallback_to_task(cls, ctx: "Context", task: Task) -> Optional[Operator]:
        # 当前任务.  fallback
        event = Receiving(task.tid, task.url.stage, None)
        return RuntimeTool.fire_event(ctx, event)

    def _fallback(self, ctx: "Context") -> Optional["Operator"]:
        # 装作没听懂.
        return RewindOperator(repeat=False)

    def destroy(self) -> None:
        return


class WithdrawOperator(AbsOperator, metaclass=ABCMeta):
    """
    """
    status: ClassVar[int]
    wrapper: Type[Withdrawing]

    def __init__(
            self,
            tid: str,
            at_stage: str | None,
    ):
        self.tid = tid
        self.at_stage = at_stage

    def _intercept(self, ctx: Context) -> Optional[Operator]:
        # 检查流程是否被拦截
        current_task = RuntimeTool.fetch_task(ctx, self.tid)
        if current_task is None:
            return None
        # 可能是一个 None
        event = self.wrapper(current_task.tid, current_task.url.stage, None)
        # 如果没有被拦截, 就继续往后走.
        return RuntimeTool.fire_event(ctx, event)

    def _run_operation(self, ctx: "Context") -> Optional["Operator"]:
        current_task = RuntimeTool.fetch_task(ctx, self.tid)
        if current_task is None:
            # 继续走后续的取消流程.
            # 没有链式取消了.
            return None

        callbacks = current_task.done(TaskStatus.DEAD, self.at_stage)
        # 保存变更.
        if not callbacks:
            return None
        callback_tasks = RuntimeTool.fetch_process_tasks_by_ids(ctx, list(callbacks))
        for task in callback_tasks:
            task.status = self.status
        RuntimeTool.store_task(*callback_tasks)
        return None

    def _fallback(self, ctx: Context) -> Optional[Operator]:
        return ScheduleOperator()

    def destroy(self) -> None:
        del self.tid


class CancelOperator(WithdrawOperator):
    status = TaskStatus.CANCELING
    wrapper = Canceling


class FailOperator(WithdrawOperator):
    status = TaskStatus.FAILING
    wrapper = Failing


class QuitOperator(WithdrawOperator):
    status = TaskStatus.CANCELING
    wrapper = Quiting

    def _intercept(self, ctx: Context) -> Optional[Operator]:
        intercepted = super()._intercept(ctx)
        if intercepted:
            RuntimeTool.set_quiting(ctx, False)
        return intercepted

    def _run_operation(self, ctx: "Context") -> Optional["Operator"]:
        after = super()._run_operation(ctx)
        RuntimeTool.set_quiting(ctx, True)
        return after


class ScheduleOperator(AbsOperator):

    def _intercept(self, ctx: "Context") -> Optional["Operator"]:
        return None

    def _run_operation(self, ctx: "Context") -> Optional["Operator"]:
        runtime = ctx.clone.runtime
        process = runtime.current_process()
        canceling = process.canceling
        if len(canceling) > 0:
            return CancelOperator(canceling[0], None)
        failing = process.failing
        if len(failing) > 0:
            return FailOperator(failing[0], None)

        fallback = process.fallback()
        tid = fallback.tid
        if fallback is not None:
            # 退出过程中, 调度会退出每一个中间任务.
            if process.quiting:
                return QuitOperator(tid, None)
            match fallback.status:
                case TaskStatus.RUNNING:
                    return ForwardOperator(tid, [])
                case _:
                    return self._preempt(ctx, tid)

        root = RuntimeTool.fetch_root_task(ctx)
        if root.status == TaskStatus.WAITING:
            # 重新回到根节点.
            return self._preempt(ctx, process.root)

        process = ctx.clone.runtime.current_process()
        if TaskStatus.is_final(root.status):
            # 如果有父进程, 就回调父进程.
            if process.parent_id:
                ctx.async_input(root.to_tasked(), pid=process.parent_id, trace=None)

        process.quiting = True
        ctx.clone.runtime.store_process(process)
        return None

    @classmethod
    def _preempt(cls, ctx: Context, tid: str) -> Optional[Operator]:
        task = RuntimeTool.fetch_task(ctx, tid)
        event = Preempting(task.tid, task.url.stage, None)
        return RuntimeTool.fire_event(ctx, event)

    def _fallback(self, ctx: "Context") -> Optional["Operator"]:
        return None

    def destroy(self) -> None:
        return None


class ResetOperator(AbsOperator):
    """
    重置进程
    """

    def _intercept(self, ctx: "Context") -> Optional["Operator"]:
        return None

    def _run_operation(self, ctx: "Context") -> Optional["Operator"]:
        runtime = ctx.clone.runtime
        process = runtime.current_process()
        process.reset()
        runtime.store_process(process)

        task = RuntimeTool.fetch_root_task(ctx)
        event = Activating(task.tid, task.url.stage, None)
        return RuntimeTool.fire_event(ctx, event)

    def _fallback(self, ctx: "Context") -> Optional["Operator"]:
        return None

    def destroy(self) -> None:
        return


class OpRestart(AbsOperator):

    def __init__(self, tid: str):
        self.tid = tid

    def _intercept(self, ctx: "Context") -> Optional["Operator"]:
        return None

    def _run_operation(self, ctx: "Context") -> Optional["Operator"]:
        task = RuntimeTool.force_fetch_task(ctx, self.tid)
        task.restart()
        RuntimeTool.store_task(ctx, task)
        return ActivateOperator(task.url, None, task.tid)

    def _fallback(self, ctx: "Context") -> Optional["Operator"]:
        return None

    def destroy(self) -> None:
        del self.tid


class DependOnOperator(AbsOperator):

    def __init__(self, tid: str, stage: str, target: URL):
        self.stage = stage
        self.tid = tid
        self.target = target

    def _intercept(self, ctx: "Context") -> Optional["Operator"]:
        """
        depend 事件可以被终止.
        """
        return None

    def _run_operation(self, ctx: "Context") -> Optional["Operator"]:
        self_task = RuntimeTool.force_fetch_task(ctx, self.tid)
        target_task = RuntimeTool.fetch_task_by_url(ctx, self.target, create=True)

        match target_task.status:
            case TaskStatus.FINISHED:
                # callback 事件
                result = RuntimeTool.task_result(ctx, target_task)
                event = Callback(self_task.tid, self_task.url.stage, target_task.url.new_with(), result)
                return RuntimeTool.fire_event(ctx, event)
            case TaskStatus.DEAD:
                # cancel 事件
                return CancelOperator(self.tid, None)
            case _:
                target_task.add_callback(self.tid)
                self_task.depend(self.stage)
                RuntimeTool.store_task(ctx, target_task, self_task)
                return ActivateOperator(target_task.url, self_task.url, target_task.tid)

    def _fallback(self, ctx: "Context") -> Optional["Operator"]:
        return None

    def destroy(self) -> None:
        del self.tid
        del self.stage
        del self.target
