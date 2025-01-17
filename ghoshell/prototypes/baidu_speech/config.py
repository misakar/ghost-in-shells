import random
import string
from typing import ClassVar

from pydantic import BaseModel


def cuid() -> str:
    return "".join(random.choice(string.ascii_letters + string.digits) for i in range(12))


class BaiduASRConfig(BaseModel):
    format = "wav"
    cuid = cuid()
    dev_pid = 1537  # 1537 表示识别普通话，使用输入法模型。根据文档填写PID，选择语言及识别模型
    asr_url = 'http://vop.baidu.com/server_api'
    scope = 'audio_voice_assistant_get'  # 有此scope表示有asr能力
    rate = 16000


class BaiduTTSConfig(BaseModel):
    # 发音人选择, 基础音库：0为度小美，1为度小宇，3为度逍遥，4为度丫丫，
    # 精品音库：5为度小娇，103为度米朵，106为度博文，110为度小童，111为度小萌，默认为度小美
    per = 4
    # 语速，取值0-15，默认为5中语速
    spd = 5
    # 音调，取值0-15，默认为5中语调
    pit = 5
    # 音量，取值0-9，默认为5中音量
    vol = 5

    # 下载的文件格式, 3：mp3(default) 4： pcm-16k 5： pcm-8k 6. wav
    aue: ClassVar[int] = 6

    cuid = cuid()

    tts_url = 'https://tsn.baidu.com/text2audio'
    scope = 'audio_tts_post'  # 有此scope表示有tts能力，没有请在网页里勾选

    FORMATS = {3: "mp3", 4: "pcm", 5: "pcm", 6: "wav"}

    @property
    def format(self) -> str:
        return self.FORMATS[self.aue]


class BaiduSpeechConfig(BaseModel):
    token_url = 'https://aip.baidubce.com/oauth/2.0/token'
    tts: BaiduTTSConfig = BaiduTTSConfig()
    asr: BaiduASRConfig = BaiduASRConfig()
