import uuid
from typing import Optional

import speech_recognition as sr
import yaml
from prompt_toolkit import prompt
from pyaudio import PyAudio
from pydantic import BaseModel
from rich.console import Console
from rich.markdown import Markdown

from ghoshell.container import Container
from ghoshell.ghost import URL
from ghoshell.messages import Input, Output
from ghoshell.messages import Text, Error
from ghoshell.shell_fmk import ShellKernel
from ghoshell.shell_protos.baidu_speech.adapter import BaiduSpeechAdapter, BaiduSpeechProvider


class BaiduSpeechShellConfig(BaseModel):
    """
    todo: 将 shell 的配置做到 configs 里.
    """
    welcome: str
    root_url: URL
    debug: bool = True


class BaiduSpeechShell(ShellKernel):
    """
    Shell
    """
    providers = [
        BaiduSpeechProvider(),
    ]

    def __init__(self, container: Container, config_path: str, runtime_path: str, config_filename: str = "config.yml"):
        self._console = Console()
        self.session_id = str(uuid.uuid4().hex)
        self.user_id = str(uuid.uuid4().hex)
        self.pyaudio = PyAudio()
        self._talking = False
        super().__init__(container, config_path, runtime_path)
        self._config: BaiduSpeechShellConfig = self._load_config(config_filename)

    def _load_config(self, config_filename: str) -> BaiduSpeechShellConfig:
        config_filename = self.config_path.rstrip("/") + "/" + config_filename.lstrip("/")
        with open(config_filename) as f:
            data = yaml.safe_load(f)
        return BaiduSpeechShellConfig(**data)

    def deliver(self, _output: Output) -> None:
        text = Text.read(_output.payload)
        if self._config.debug:
            self._console.print(_output.payload)
        if text is not None:
            self._markdown_print(text.content)
            self._speak_text(text.content)
        err = Error.read(_output.payload)
        if err is not None:
            self._markdown_print(f"""
# error {err.errcode} occur

{err.errmsg}

{err.stack_info}
""")

    def on_event(self, e: str) -> Optional[Input]:
        content = e.strip()
        if content == "/exit":
            self._console.print("bye!")
            exit(0)

        trace = dict(
            clone_id=self.session_id,
            session_id=self.session_id,
            shell_id=self.session_id,
            shell_kind="audio_shell",
            subject_id=self.user_id,
        )
        text = Text(content=content)
        return Input(
            mid=uuid.uuid4().hex,
            url=self._config.root_url.dict(),
            payload=text.as_payload_dict(),
            trace=trace,
        )

    def run_as_app(self) -> None:
        self._welcome()
        while True:
            user_input = prompt("<<< ")
            match user_input:
                case "/exit":
                    self._console.print("Bye!")
                    exit(0)
                case "":
                    self._listen()
                case _:
                    self.tick(user_input)

    def tick(self, text: str) -> None:
        self._console.print("> waiting ghost...")
        super().tick(text)

    def _welcome(self) -> None:
        """
        todo: welcome
        """
        self._markdown_print(self._config.welcome)

    def _listen(self) -> None:
        adapter = self.container.force_fetch(BaiduSpeechAdapter)

        r = sr.Recognizer()
        with sr.Microphone() as source:
            self._console.print("> listening...")
            audio = r.listen(source)
        wave_data = audio.get_wav_data(convert_rate=16000)
        self._console.print("> speech to text...")
        text = adapter.wave2text(wave_data)
        self._console.print("> you said: " + text)
        self._talking = True
        self.tick(text)
        self._talking = False

    def _speak_text(self, text: str) -> None:
        """
        说话.
        """
        adapter = self.container.force_fetch(BaiduSpeechAdapter)
        self._console.print("> text to speech...")
        speech_data = adapter.text2speech(text)
        self._speak(speech_data)

    def _speak(self, wave_data: bytes) -> None:
        self._console.print("> speaking...")
        p = self.pyaudio
        stream = p.open(
            format=p.get_format_from_width(2),
            channels=1,
            rate=16000,
            output=True,
        )
        # play stream
        stream.write(wave_data)
        # stop stream
        stream.stop_stream()
        stream.close()

    def _markdown_print(self, text: str) -> None:
        markdown = Markdown(f"""
----

{text}
""")
        self._console.print(markdown)
