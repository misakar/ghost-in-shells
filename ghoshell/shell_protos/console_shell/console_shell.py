import asyncio
import uuid
from typing import List, Optional, ClassVar

from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.shortcuts import PromptSession
from rich.console import Console
from rich.markdown import Markdown

from ghoshell.ghost import Ghost
from ghoshell.messages import *
from ghoshell.shell import Messenger
from ghoshell.shell_fmk import InputMiddleware, OutputMiddleware
from ghoshell.shell_fmk import ShellKernel, Bootstrapper
from ghoshell.shell_fmk import SyncGhostMessenger, MockMessageQueue
from ghoshell.shell_protos.console_shell.pipelines import InputTestMiddleware


class ConsoleShell(ShellKernel):
    KIND: ClassVar[str] = "console"

    # 初始化流程
    bootstrapping: ClassVar[List[Bootstrapper]] = []

    # 输入处理
    input_middlewares: ClassVar[List[InputMiddleware]] = [
        InputTestMiddleware()
    ]

    # 输出处理
    output_middlewares: ClassVar[List[OutputMiddleware]] = [
    ]

    def __init__(self, ghost: Ghost):
        messenger = SyncGhostMessenger(ghost, queue=MockMessageQueue())
        self.session_id = str(uuid.uuid4().hex)
        self.user_id = str(uuid.uuid4().hex)
        self.app = Console()
        self.ghost = ghost
        super().__init__(ghost.container, messenger)

    def kind(self) -> str:
        return "command_shell"

    def run_as_app(self):
        asyncio.run(self._main())

    async def _main(self):
        with patch_stdout(raw=True):
            background_task = asyncio.create_task(self.handle_async_output())
            try:
                await self._prompt_loop()
            finally:
                background_task.cancel()
            self.app.print("Quitting event loop. Bye.")

    async def _prompt_loop(self):
        session = PromptSession("\n\n<<< ", )
        bindings = KeyBindings()

        @bindings.add("c-p")
        def key_post(prompt_event):
            self.app.print(prompt_event)

        while True:
            try:
                event = await session.prompt_async(multiline=False, key_bindings=bindings)
                self.tick(event)
            except (EOFError, KeyboardInterrupt):
                self.app.print(f"quit!!")
                exit(0)

    def on_event(self, prompt: str) -> Optional[Input]:
        trace = dict(
            clone_id=self.session_id,
            session_id=self.session_id,
            shell_id=self.session_id,
            shell_kind=self.kind(),
            subject_id=self.user_id,
        )
        text = Text(content=prompt)
        return Input(
            mid=uuid.uuid4().hex,
            payload=text.as_payload_dict(),
            trace=trace,
        )

    def deliver(self, _output: Output) -> None:
        text = Text.read(_output.payload)
        if text is not None and not text.is_empty():
            self.app.print(Markdown(text.content))

    def messenger(self, _input: Input | None) -> Messenger:
        return self._messenger