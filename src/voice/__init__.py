"""语音：先听后说（0.4.6 F 组，6-1）。

* 听（STT）：faster-whisper（MIT，首选；模型权重也是 MIT）；
  后路是 whisper.cpp（MIT，子进程调用）。
* 说（TTS）：Kokoro-82M（Apache-2.0，代码与权重同许可），走
  ``kokoro-onnx``（轻，不拉 torch）；torch 版 ``kokoro`` 作后路。

两条硬规矩：

1. **离线本机跑**：录音只在本机转文字、只在本机合成语音，不送上云。
2. **没装就老实说**：引擎包、模型文件都不在代码仓里，也不随基础包
   安装；第一次用时按错误提示里的步骤装，装之前接口返回 503 +
   中文原因 + 安装提示，绝不返回编造的识别/合成结果。

中文 TTS 的许可提醒（已核实，见 docs/voice-ocr-calendar-mobile.md）：
Kokoro 中文发音要走 ``misaki[zh]``，它在个别生僻词上回落到系统
``espeak-ng``（GPL-3.0）。espeak-ng **不随安装包分发**，用户需要时
自行从系统包管理器 / 官网安装；没装时中文 TTS 明确报错，不静默
降级成错音。
"""

from .stt import transcribe_audio  # noqa: F401
from .tts import synthesize_speech  # noqa: F401
