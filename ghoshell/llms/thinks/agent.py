import json
import os
from abc import abstractmethod, ABCMeta
from typing import Dict, List, Callable, Type, Optional, AnyStr, Iterator

import yaml
from pydantic import BaseModel, Field

from ghoshell.ghost import Think, Event, OnReceived, CtxTool, Stage, ThinkMeta, Reaction, Intention, ThinkDriver, Focus
from ghoshell.ghost import Thought, Operator, Context, URL, Mindset
from ghoshell.ghost_fmk.stages import BasicStage
from ghoshell.llms import OpenAIChatMsg, OpenAIChatCompletion, OpenAIFuncSchema
from ghoshell.messages import Text
from ghoshell.utils import import_module_value

AGENT_THINK_DRIVER_NAME = "llm_agent_driver"


# ----- configs ----- #

class AgentStageConfig(BaseModel):
    """
    支持函数的对话状态 - 配置
    """

    name: str

    # prompt 所用的系统提示. role==system, 会在对话记录最前面.
    instruction: str = ""

    # 类名.
    class_name: str = ""

    # 当前状态的自我描述.
    desc: str = ""

    # 可以被引用的 func. 会通过 importlib 来引用.
    llm_funcs: List[str] = Field(default_factory=list)

    # 使用类的方法作为函数, 方法必须存在, 并且通过 llm_func_decorator 封装过.
    methods_as_funcs: List[str] = Field(default_factory=lambda: [
        "op_finish",
        "op_restart",
        "op_cancel",
    ])

    # 将其它状态位作为 func 引用. stage_name 就是方法名.
    # 如果需要取一个别名, 则用 : 隔开. 比如 `stage_name:func_alias`
    stages_as_func: List[str] = Field(default_factory=list)

    # 将其它 think 作为 func 引用. stage_name 就是方法名.
    # 如果需要取一个别名, 则用 : 隔开. 比如 `stage_name:func_alias`
    thinks_as_func: List[str] = Field(default_factory=list)

    # openai chat completion 的 func call 字段.
    default_func_call: str = "auto"

    # 调用大模型时是否有指定的 config
    llm_config_name: str = ""

    # 启动时是否要使用一个 prompt 来引导对话. 这个 prompt 也会调用 function
    # 如果为空的话, 不会调用.
    initial_prompt: str = ""

    # 接受到消息时默认的 prompt.
    on_receive_prompt: str = ""

    extra: Dict = Field(default_factory=lambda: {})


class AgentThinkConfig(BaseModel):
    # think name. 文件配置时如果缺省, 会用文件名加默认前缀来自动生成.
    name: str

    instruction: str

    default_stage: AgentStageConfig

    # think desc
    desc: str = ""

    welcome: str = ""

    # 类名.
    class_name: str = ""

    # import path of args class
    args_type: str | None = None

    # import path of thought class
    thought_type: str | None = None

    # 调用大模型时是否有指定的 config
    llm_config_name: str = ""

    stages: List[AgentStageConfig] = Field(default_factory=list)

    def as_think_meta(self) -> ThinkMeta:
        return ThinkMeta(
            id=self.name,
            kind=AGENT_THINK_DRIVER_NAME,
            config=self.dict(),
        )


# ----- thought ----- #


class AgentThoughtData(BaseModel):
    """
    支持函数的对话上下文.
    可以通过重写, 添加额外的参数.
    """
    dialog: List[OpenAIChatMsg] = Field(default_factory=lambda: [])

    # 初始化的上下文.
    think_instruction: str = ""

    def add_user_message(self, message: str, name: str | None = None):
        msg = OpenAIChatMsg(
            role=OpenAIChatMsg.ROLE_USER,
            name=name,
            content=message,
        )
        self.dialog.append(msg)

    def add_ai_message(self, message: str, name: str | None = None):
        self.dialog.append(OpenAIChatMsg(
            role=OpenAIChatMsg.ROLE_ASSISTANT,
            name=name,
            content=message,
        ))

    def add_system_message(self, message: str):
        self.dialog.append(OpenAIChatMsg(
            role=OpenAIChatMsg.ROLE_SYSTEM,
            content=message,
        ))


class AgentThought(Thought):
    """
    支持函数的有状态思维.
    可以通过重写, 得到额外的参数.
    """
    data: AgentThoughtData | None = None

    def say(self, ctx: Context, message: str, name: str | None = None):
        ctx.send_at(self).text(message)
        self.data.add_ai_message(message, name)

    @classmethod
    def data_wrapper(cls) -> Type[AgentThoughtData]:
        """
        可以重写, 从而得到别的容器.
        """
        return AgentThoughtData

    def prepare(self, args: Dict) -> None:
        if self.data is None:
            wrapper = self.data_wrapper()
            self.data = wrapper()

    def set_variables(self, variables: Dict) -> None:
        wrapper = self.data_wrapper()
        self.data = wrapper(**variables)

    def vars(self) -> Dict | None:
        data = self.data.dict()
        return data

    def _destroy(self) -> None:
        del self.data


# ----- functions ----- #

# 定义在 stage 类中的, 响应一个大模型函数调用的类方法.
LLMCallable = Callable[[Context, Thought, BaseModel | None], Operator | None]


class LLMFunc(metaclass=ABCMeta):
    """
    LLM 函数融合 Ghoshell 的方法封装.
    """

    @abstractmethod
    def name(self) -> str:
        pass

    @abstractmethod
    def schema(self, ctx: Context, this: AgentThought) -> OpenAIFuncSchema:
        pass

    @abstractmethod
    def call(self, ctx: Context, this: Thought, params: Dict | None) -> Operator | None:
        pass


class MethodAsFunc(LLMFunc):
    """
    可以被大模型驱动, 又和 Ghoshell 机制一致的 function.
    """

    def __init__(
            self,
            name: str,  # 对于 LLM 而言的方法名
            desc: str,  # 对于 LLM 的描述
            caller: LLMCallable,  # 可运行的方法.
            params_type: Type[BaseModel] | None,  # 参数类型. 用 BaseModel 来处理.
    ):
        self._name = name
        self._desc = desc
        self._caller = caller
        self._params_type = params_type

    def name(self) -> str:
        return self._name

    def schema(self, ctx: Context, this: AgentThought) -> OpenAIFuncSchema:
        return OpenAIFuncSchema(
            name=self._name,
            desc=self._desc,
            parameters_schema=self._params_type.schema() if self._params_type is not None else None
        )

    def call(self, ctx: Context, this: Thought, params: Dict | None) -> Operator | None:
        """
        支持被大模型的返回结果调用这个方法.
        """
        wrapped_params = None
        if self._params_type is not None:
            wrapped_params = self._params_type(**params) if params is not None else self._params_type()
        return self._caller(ctx, this, wrapped_params)


class RedirectFunc(LLMFunc):
    """
    可以重定向到其它 think
    """

    def __init__(
            self,
            name: str,
            think: str
    ):
        self._name = name
        self._think = think

    def name(self) -> str:
        return self._name

    def schema(self, ctx: Context, this: AgentThought) -> OpenAIFuncSchema:
        think = CtxTool.force_fetch_think(ctx, self._think)
        args_type = think.args_type()
        return OpenAIFuncSchema(
            name=self._name,
            desc=think.desc(ctx, None),
            parameters_schema=args_type.schema() if args_type else None,
        )

    def call(self, ctx: Context, this: Thought, params: Dict | None) -> Operator | None:
        """
        跳转到另一个会话.
        """
        url = URL.new(resolver=self._think, args=params)
        return ctx.mind(this).redirect(url)


class StageAsFunc(LLMFunc):
    """
    将一个 stage 变为方法.
    """

    def __init__(
            self,
            name: str,
            stage: str,
    ):
        self._name = name
        self._stage = stage

    def name(self) -> str:
        return self._name

    def schema(self, ctx: Context, this: AgentThought) -> OpenAIFuncSchema:
        stage = CtxTool.current_think_stage(ctx)
        return OpenAIFuncSchema(
            name=self._name,
            desc=stage.desc(ctx, this),
        )

    def call(self, ctx: Context, this: Thought, params: Dict | None) -> Operator | None:
        return ctx.mind(this).forward(self._stage)


# ----- decorators ----- #

def llm_func_decorator(
        name: str,
        params_type: BaseModel | None = None,
        desc: str = "",  # desc 为空, 则会用方法的 __doc__ 作为 desc
) -> Callable[[LLMCallable], LLMFunc]:
    """
    decorator, 将一个方法封装成一个 LLM 函数
    """

    def decorator(method: LLMCallable) -> LLMFunc:
        return MethodAsFunc(
            name=name,
            desc=desc if desc else method.__doc__.strip(),
            caller=method,
            params_type=params_type,
        )

    return decorator


def llm_func_redirect(name: str, think: str) -> LLMFunc:
    """
    将一个 Think 作为一个 llm func, 用来重定向到别的会话.
    """
    return RedirectFunc(name=name, think=think)


def llm_func_staging(name: str, stage: str) -> LLMFunc:
    """
    将一个 stage 作为 func.
    """
    return StageAsFunc(name=name, stage=stage)


# ---- think ---- #

class AgentThink(Think, Stage):
    """
    基于大模型驱动, 完成一个思维链
    目标:
    1. 可以配置化
    2. 可以有多个步骤
    3. 支持强类型的函数调用.
    4. 每个步骤支持不同的函数调用.
    5. 支持持续的内部思考
    """

    def __init__(self, config: AgentThinkConfig):
        self.config = config

    def url(self) -> URL:
        return URL.new(resolver=self.config.name)

    def to_meta(self) -> ThinkMeta:
        return ThinkMeta(
            id=self.config.name,
            kind=AGENT_THINK_DRIVER_NAME,
            config=self.config.dict(),
        )

    def desc(self, ctx: Context, thought: Thought | None) -> AnyStr:
        return self.config.desc

    def new_task_id(self, ctx: "Context", args: Dict) -> str:
        return self.url().new_id()

    def new_thought(self, ctx: "Context", args: Dict) -> Thought:
        if self.config.thought_type is not None:
            thought_type: Type[AgentThought] = import_module_value(self.config.thought_type)
            return thought_type(args)
        return AgentThought(args)

    @abstractmethod
    def result(self, ctx: Context, this: Thought) -> Optional[Dict]:
        pass

    def all_stages(self) -> List[str]:
        stage_names = {stage_config.name for stage_config in self.config.stages}
        stage_names.add("")
        stage_names.add(self.config.default_stage.name)
        return list(stage_names)

    def fetch_stage(self, stage_name: str = "") -> Optional[Stage]:
        if stage_name == "":
            return self

        if stage_name == self.config.default_stage.name:
            return self._make_stage(self.config.default_stage)

        for config in self.config.stages:
            if config.name == stage_name:
                return self._make_stage(config)
        return None

    def _make_stage(self, config: AgentStageConfig) -> Stage:
        wrapper: Type[AgentStage] = DefaultAgentStage
        if not config.llm_config_name:
            # 默认每个 stage 使用的 llm config name 都和 think 的一致.
            config.llm_config_name = self.config.llm_config_name

        if config.class_name:
            wrapper = import_module_value(config.class_name)
        return wrapper(self.config.name, config)

    def intentions(self, ctx: Context) -> List[Intention] | None:
        return None

    def args_type(self) -> Type[BaseModel] | None:
        if self.config.args_type is None:
            return None
        return import_module_value(self.config.args_type)

    def reactions(self) -> Dict[str, Reaction]:
        return {}

    def on_event(self, ctx: "Context", this: AgentThought, event: Event) -> Operator | None:
        # 实现背景介绍.
        if self.config.welcome:
            this.say(ctx, self.config.welcome)

        this.data.think_instruction = self.config.instruction
        return ctx.mind(this).forward(self.config.default_stage.name)


class AgentStage(BasicStage, metaclass=ABCMeta):
    """
    一个支持 llm function call 模式的 stage 实现.
    """

    def __init__(
            self,
            think: str,
            config: AgentStageConfig
    ):
        """
        func stage 可以重写 init 方法, 得到不同的 config
        """
        self.think_name = think
        self.config = config
        self._cached_funcs: List[LLMFunc] | None = None

    def url(self) -> URL:
        return URL(
            resolver=self.think_name,
            stage=self.think_name,
        )

    def desc(self, ctx: Context, this: Thought | None) -> str:
        return self.config.desc

    def on_activating(self, ctx: "Context", this: AgentThought, e: Event) -> Operator | None:
        """
        当前 stage 启动时的动作.
        给出一个默认的实现.
        """
        if self.config.initial_prompt:
            return self.call_llm_with_funcs(ctx, this, self.config.initial_prompt)
        return ctx.mind(this).awaits()

    @abstractmethod
    def on_received(self, ctx: "Context", this: AgentThought, e: OnReceived) -> Operator | None:
        """
        当前 stage 进入 await 状态, 并且得到了用户消息的时候.
        给出一个默认的实现. 实际情况, 尤其是有多种消息的情况, 还是需要自行实现.
        """
        pass

    @abstractmethod
    def after_func_called(self, ctx: Context, this: AgentThought) -> Operator:
        """
        如果一个 method 请求完毕了, 仍然没有后续反馈时, 就会调用这个方法.
        默认的做法是, 不断重复当前 stage, 直到有结果.
        """
        pass

    def on_llm_text_message(self, ctx: Context, this: AgentThought, message: str) -> Operator:
        """
        llm 返回了一个文字消息, 而不是函数调用.
        """
        this.say(ctx, message)
        return ctx.mind(this).awaits()

    def on_llm_null_resp(self, ctx: Context, this: AgentThought) -> Operator:
        """
        llm 没有返回任何有效结果时 (很可能由于 prompt 自身导致?), 采取的行动.
        """
        return ctx.mind(this).awaits()

    def llm_funcs(self) -> List[LLMFunc]:
        """
        缓存一下
        """
        if self._cached_funcs is None:
            self._cached_funcs = self._llm_funcs()
        return self._cached_funcs

    def _llm_funcs(self) -> List[LLMFunc]:
        """
        当前状态可以被 llm 调用的 funcs
        也可以人工修改加入新的方法.
        """
        done = set()
        funcs = []
        if self.config.llm_funcs:
            for fn_path in self.config.llm_funcs:
                value: LLMFunc = import_module_value(fn_path)
                name = value.name()
                if name in done:
                    continue
                else:
                    done.add(name)
                funcs.append(value)

        if self.config.methods_as_funcs:
            for method_name in self.config.methods_as_funcs:
                method = getattr(self, method_name)
                if isinstance(method, LLMFunc):
                    funcs.append(method)

        if self.config.stages_as_func:
            for stage in self.config.stages_as_func:
                parts = stage.split(":")
                stage_name = parts[0]
                func_name = parts[1] if len(parts) > 1 else stage_name
                if func_name in done:
                    continue
                else:
                    done.add(func_name)
                funcs.append(llm_func_staging(name=func_name, stage=stage_name))

        if self.config.thinks_as_func:
            for think in self.config.thinks_as_func:
                parts = think.split(":")
                think_name = parts[0]
                func_name = parts[1] if len(parts) > 1 else think_name
                if func_name in done:
                    continue
                else:
                    done.add(func_name)
                funcs.append(llm_func_redirect(name=func_name, think=think_name))
        return funcs

    def get_funcs_schemas(self, ctx: Context, this: AgentThought) -> List[OpenAIFuncSchema]:
        """
        获得所有方法的 openai function schemas
        """
        funcs = self.llm_funcs()
        return [fn.schema(ctx, this) for fn in funcs]

    def call_llm_with_funcs(self, ctx: Context, this: AgentThought, prompt: str | None) -> Operator:
        """
        运行 prompt.
        """
        # 准备 prompter
        prompter = ctx.container.force_fetch(OpenAIChatCompletion)
        session_id = ctx.input.trace.session_id

        # think 的全局 instruction
        chat_context: List[OpenAIChatMsg] = [
            OpenAIChatMsg(
                role=OpenAIChatMsg.ROLE_SYSTEM,
                content=this.data.think_instruction.format(name=this.url.resolver),
            )
        ]

        # 处理有参数的情况.
        think = CtxTool.force_fetch_think(ctx, this.url.resolver)
        args_type = think.args_type()
        if args_type is not None:
            chat_context.append(
                OpenAIChatMsg(
                    role=OpenAIChatMsg.ROLE_SYSTEM,
                    content="args schema: " + json.dumps(args_type.schema())
                )
            )
            chat_context.append(
                OpenAIChatMsg(
                    role=OpenAIChatMsg.ROLE_SYSTEM,
                    content="args: " + json.dumps(this.url.args)
                )
            )

        # stage instruction
        if self.config.instruction:
            name = self.url().stage
            desc = self.desc(ctx, this)
            chat_context.append(OpenAIChatMsg(
                role=OpenAIChatMsg.ROLE_SYSTEM,
                content=self.config.instruction.format(name=name, desc=desc),
            ))

        # 输入上下文.
        for m in this.data.dialog:
            chat_context.append(m.copy())

        # 加入最后的提示.
        if prompt:
            chat_context.append(OpenAIChatMsg(
                role=OpenAIChatMsg.ROLE_SYSTEM,
                content=prompt,
            ))

        # 加入 functions.
        func_schemas = self.get_funcs_schemas(ctx, this)
        choice = prompter.chat_completion(
            session_id,
            chat_context,
            func_schemas,
            function_call=self.config.default_func_call,
            config_name=self.config.llm_config_name,
        )

        # 如果是一般的消息, 走消息处理.
        msg = choice.as_chat_msg()
        if msg is not None:
            return self.on_llm_text_message(ctx, this, msg.content)

        # 如果返回值是 函数, 走函数处理.
        called = choice.get_function_called()
        if called is not None:
            params = choice.get_function_params()
            return self.call_llm_func(ctx, this, called, params)
        op = self.on_llm_null_resp(ctx, this)
        return op

    def call_llm_func(
            self,
            ctx: Context,
            this: AgentThought,
            called: str | None,
            params: Dict | None,
    ) -> Operator:
        """
        运行一个 method.
        """
        mapping = {fn.name(): fn for fn in self.llm_funcs()}
        if called not in mapping:
            return self.on_llm_null_resp(ctx, this)
        fn: LLMFunc = mapping[called]
        op = fn.call(ctx, this, params)
        # 如果方法返回了任何信息, 就继续.
        if op is not None:
            return op
        return self.after_func_called(ctx, this)

    @llm_func_decorator("op_finish")
    def op_finish(self, ctx: Context, this: AgentThought, params: None) -> Operator | None:
        """
        finish current task
        """
        return ctx.mind(this).finish()

    @llm_func_decorator("op_restart")
    def op_restart(self, ctx: Context, this: AgentThought, params: None) -> Operator | None:
        """
        clear dialog context, restart conversation
        """
        return ctx.mind(this).restart()

    @llm_func_decorator("op_cancel")
    def op_cancel(self, ctx: Context, this: AgentThought, params: None) -> Operator | None:
        """
        cancel current task
        """
        return ctx.mind(this).cancel()


class DefaultAgentStage(AgentStage):
    """
    默认 function stage 的实现.
    """

    def on_received(self, ctx: "Context", this: AgentThought, e: OnReceived) -> Operator | None:
        text = ctx.read(Text)
        if text is not None:
            this.data.add_user_message(text.content)
            return self.call_llm_with_funcs(ctx, this, self.config.on_receive_prompt)
        return ctx.mind(this).rewind()

    def after_func_called(self, ctx: Context, this: AgentThought) -> Operator:
        return ctx.mind(this).repeat()

    def intentions(self, ctx: Context) -> List[Intention] | None:
        return None

    def reactions(self) -> Dict[str, Reaction]:
        return {}


class DefaultAgentThink(AgentThink):
    """
    默认的 Think 的实现.
    """

    def result(self, ctx: Context, this: Thought) -> Optional[Dict]:
        return None


# todo: 还要解决 depend on 这种情况. 可以用来分割上下文.

# ---- mindset and driver ---- #


class FileAgentMindset(Mindset, ThinkDriver):

    def __init__(self, dirname: str, prefix: str | None = None):
        self._dirname = dirname
        if not prefix:
            prefix = "agents"
        self._prefix = prefix
        self._cached_thinks: Dict[str, AgentThink] = {}
        self._cached_think_configs: Dict[str, AgentThinkConfig] = {}
        self._load_configs()

    def _load_configs(self):
        config_path = self._dirname
        for root, ds, fs in os.walk(config_path):
            for filename in fs:
                if not filename.endswith(".yaml"):
                    continue
                basename = filename[:len(filename) - 5]
                full_filename = config_path.rstrip("/") + "/" + filename
                with open(full_filename) as f:
                    data = yaml.safe_load(f)
                    name = data.get("name", None)
                    if name is None:
                        name = self._prefix.rstrip("/") + "/" + basename
                        data["name"] = name
                    config = AgentThinkConfig(**data)
                    self._cached_think_configs[name] = config

    @property
    def focus(self) -> Focus:
        raise NotImplementedError("focus not implemented")

    def clone(self, clone_id: str) -> Mindset:
        return self

    def fetch(self, thinking: str) -> Optional[Think]:
        think = self._cached_thinks.get(thinking, None)
        if think is not None:
            return think

        config = self._cached_think_configs.get(thinking, None)
        if config is None:
            return None
        return self._make_think(config)

    def fetch_meta(self, thinking: str) -> Optional[ThinkMeta]:
        config = self._cached_think_configs.get(thinking, None)
        if config is None:
            return None
        return config.as_think_meta()

    def register_sub_mindset(self, mindset: Mindset) -> None:
        raise NotImplementedError("register_sub_mindset not implemented")

    def register_driver(self, driver: ThinkDriver) -> None:
        raise NotImplementedError("register_driver not implemented")

    def get_driver(self, driver_name: str) -> ThinkDriver | None:
        if driver_name == AGENT_THINK_DRIVER_NAME:
            return self
        return None

    def register_meta(self, meta: ThinkMeta) -> None:
        raise NotImplementedError("register_meta not implemented")

    def foreach_think(self) -> Iterator[Think]:
        for name in self._cached_think_configs:
            config = self._cached_think_configs[name]
            yield self._make_think(config)

    def driver_name(self) -> str:
        return AGENT_THINK_DRIVER_NAME

    def from_meta(self, meta: ThinkMeta) -> "Think":
        if meta.id in self._cached_thinks:
            return self._cached_thinks[meta.id]

        config = AgentThinkConfig(**meta.config)
        return self._make_think(config)

    def _make_think(self, config: AgentThinkConfig) -> AgentThink:
        name = config.name
        if name in self._cached_thinks:
            return self._cached_thinks[name]

        wrapper = DefaultAgentThink
        class_name = config.class_name
        if class_name:
            wrapper: Type[DefaultAgentThink] = import_module_value(class_name)
        think = wrapper(config)
        self._cached_thinks[config.name] = think
        return think

    def destroy(self) -> None:
        del self._cached_thinks
        del self._cached_think_configs