"""语音 HTTP 面（0.4.6 F 组，6-1）。

* ``GET /v1/voice/status`` — STT/TTS 装好没有、用的哪个引擎、模型放哪。
* ``POST /v1/voice/transcribe`` — 录音文件 -> 文字（再由前端把文字发给助手）。
* ``POST /v1/voice/speak`` — 文字 -> wav 音频（前端播放）。

引擎没装时一律 503 + 中文原因 + 安装提示，不编结果。录音与文字
只在本机处理，不送上云。挂载时由 ``main.py`` 加人类主权闸门。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from src.common.engines import EngineUnavailableError
from src.voice.settings import (
    DEFAULT_INTERRUPT_COOLDOWN_MS,
    DEFAULT_INTERRUPT_MIN_MS,
    SILENCE_OPTIONS_MS,
    load_voice_settings,
    save_voice_settings,
)
from src.voice.providers import (
    get_stt_provider,
    get_tts_provider,
    voice_pipeline_status,
)
from src.voice.stt import stt_status, transcribe_audio
from src.voice.tts import ZH_VOICES, DEFAULT_VOICE, synthesize_speech, tts_status

from .account_context import AccountContext, deny, require_account_principal

router = APIRouter(prefix="/v1/voice", tags=["voice"])


def _require_self(context: AccountContext) -> str:
    """声纹只管自己的账号：ViewAs 只读视角下，主账号也看不到子账号的声纹。

    与 ``src/gateway/totp.py`` 的同名函数同一口径：视角一开就 403，
    绝不静默回落到查看者本人；写操作在路由前还会被 ViewAs 中间件拦一次。
    """
    if getattr(context, "viewed_as_by", None):
        raise deny(403, "ACCOUNT_SCOPE_DENIED", "无权执行该操作")
    return context.principal


class SpeakRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)
    voice: str = Field(default=DEFAULT_VOICE)
    #: 语速倍率 0.5–2.0，默认 1.0（正常语速）；合成时生效，音调不变。
    speed: float = Field(default=1.0, ge=0.5, le=2.0)


@router.get("/status")
def voice_status() -> Dict[str, Any]:
    # 大升级 U3：状态走 provider 抽象（本机默认 / 云端显式配置），
    # note 按实际 provider 说实话——配了云端就不许再写"不送上云"。
    pipeline = voice_pipeline_status()
    cloud = any(value == "openai" for value in pipeline["providers"].values())
    return {
        "stt": pipeline["stt"],
        "tts": pipeline["tts"],
        "wake": pipeline["wake"],
        "providers": pipeline["providers"],
        "session": pipeline.get("session"),
        "ready": pipeline["ready"],
        "note": (
            "已配置云端语音引擎：录音/文字会发到所配置的云端服务。"
            if cloud
            else "语音全程离线本机跑，录音不送上云。"
        ),
    }


@router.post("/transcribe")
async def voice_transcribe(
    file: UploadFile = File(...),
    language: str = "zh",
) -> Dict[str, Any]:
    data = await file.read()
    try:
        result = await asyncio.to_thread(
            get_stt_provider().transcribe,
            data, file.filename or "recording.webm", language,
        )
    except EngineUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail={"message": exc.detail, "install_hint": exc.install_hint},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=f"语音识别失败：{exc}") from exc
    return {
        "text": result.text,
        "language": result.language,
        "engine": result.engine,
        "model": result.model,
    }


@router.post("/speak")
async def voice_speak(body: SpeakRequest) -> Response:
    try:
        result = await asyncio.to_thread(
            get_tts_provider().synthesize, body.text, body.voice, body.speed
        )
    except EngineUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail={"message": exc.detail, "install_hint": exc.install_hint},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=f"语音合成失败：{exc}") from exc
    return Response(
        content=result.wav_bytes,
        media_type="audio/wav",
        headers={
            "Content-Disposition": 'attachment; filename="reply.wav"',
            "X-Voice": result.voice,
            "X-Engine": result.engine,
        },
    )


class TurnReportRequest(BaseModel):
    """前端上报一轮连续对话的诊断信息（只含文字与耗时，不含音频）。"""

    conversation_id: str = Field(..., min_length=1, max_length=200)
    turn_id: str = Field(default="")
    transcript: str = Field(default="", max_length=5000)
    reply_chars: int = Field(default=0, ge=0)
    interrupted: bool = Field(default=False)
    interrupt_reason: str = Field(default="", max_length=500)
    timings_ms: Dict[str, Any] = Field(default_factory=dict)


@router.post("/turns")
def report_voice_turn(body: TurnReportRequest) -> Dict[str, Any]:
    """上报一轮对话的诊断记录（验收对表用；不进聊天历史，不存音频）。"""
    from src.voice.turns import finish_turn, note_timing, start_turn

    turn_id = body.turn_id
    finished = finish_turn(
        turn_id,
        transcript=body.transcript,
        reply_chars=body.reply_chars,
        interrupted=body.interrupted,
        interrupt_reason=body.interrupt_reason,
    ) if turn_id else None
    if finished is None:
        # 没给 turn_id，或给的轮次不存在（例如网关重启过）：新建一条再收尾。
        created = start_turn(body.conversation_id)
        turn_id = created["turn_id"]
        finished = finish_turn(
            turn_id,
            transcript=body.transcript,
            reply_chars=body.reply_chars,
            interrupted=body.interrupted,
            interrupt_reason=body.interrupt_reason,
        )
    for key, value in (body.timings_ms or {}).items():
        if key in ("speech_end_to_transcribe", "transcribe", "first_token", "first_audio"):
            try:
                note_timing(turn_id, key, float(value))
            except (TypeError, ValueError):
                continue
    return {"turn": finished}


@router.get("/settings")
def voice_settings() -> Dict[str, Any]:
    """当前语音调参（静音阈值四档 + 打断阈值；只含数字）。"""
    return load_voice_settings()


class VoiceSettingsUpdate(BaseModel):
    """保存语音调参：静音阈值只认 400/600/800/1200 四档（7-5 §5.1）。"""

    min_silence_duration_ms: int = Field(..., description="静音满多久算说完（毫秒）")
    interrupt_min_ms: int = Field(default=DEFAULT_INTERRUPT_MIN_MS, ge=100, le=1000)
    interrupt_cooldown_ms: int = Field(default=DEFAULT_INTERRUPT_COOLDOWN_MS, ge=0, le=2000)


@router.put("/settings")
def update_voice_settings(body: VoiceSettingsUpdate) -> Dict[str, Any]:
    if body.min_silence_duration_ms not in SILENCE_OPTIONS_MS:
        raise HTTPException(
            status_code=400,
            detail="静音阈值只能是 400 / 600 / 800 / 1200（毫秒）。",
        )
    try:
        return save_voice_settings(
            min_silence_duration_ms=body.min_silence_duration_ms,
            interrupt_min_ms=body.interrupt_min_ms,
            interrupt_cooldown_ms=body.interrupt_cooldown_ms,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/voiceprint/status")
def voiceprint_status_endpoint(
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """声纹认人状态：只报**自己**的注册情况（主账号也看不到子账号的）。

    没模型/没注册时如实报缺什么，不编“已接入”；不触发模型下载。
    """
    from src.voice.voiceprint import voiceprint_status

    return voiceprint_status(_require_self(context))


@router.post("/voiceprint/model/download")
def voiceprint_model_download_endpoint(
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """开始按需下载说话人特征模型（约 38MB，联网一次，之后离线可用）。

    模型不随安装包带走：装机时不下，打开声纹点一次才下；下完验大小与
    SHA-256 再落盘，来源与许可见状态里的 model_source / model_license。
    """
    from src.voice.voiceprint import start_model_download

    _require_self(context)
    return start_model_download()


@router.get("/voiceprint/model/download")
def voiceprint_model_download_status_endpoint(
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """模型下载进度（started / downloaded / total / done / error）。"""
    from src.voice.voiceprint import model_download_status

    _require_self(context)
    return model_download_status()


@router.post("/voiceprint/enroll")
async def voiceprint_enroll_endpoint(
    files: list[UploadFile] = File(...),
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """注册声纹：一次传 1–3 句录音（每句 1–30 秒，建议 3 句）。

    录音只用来当场提特征，提完就丢，不存音频；存下的是加密后的声纹
    特征（只存本机、只有本人能看、不进备份）。重新注册会覆盖上一次。
    """
    from src.voice.voiceprint import decode_audio, enroll

    principal = _require_self(context)
    if not files:
        raise HTTPException(status_code=400, detail="注册至少要录 1 句。")
    if len(files) > 3:
        raise HTTPException(status_code=400, detail="一次最多录 3 句，多了请分两次注册（后一次会覆盖前一次）。")
    clips = []
    for upload in files:
        data = await upload.read()
        try:
            clips.append((decode_audio(data, upload.filename or "recording.wav"), upload.filename or "recording.wav"))
        except EngineUnavailableError as exc:
            raise HTTPException(
                status_code=503,
                detail={"message": exc.detail, "install_hint": exc.install_hint},
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        return enroll(principal, clips)
    except EngineUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail={"message": exc.detail, "install_hint": exc.install_hint},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/voiceprint/verify")
async def voiceprint_verify_endpoint(
    file: UploadFile = File(...),
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """验证一下：这段声音是不是你本人（返回认没认出、分数与阈值）。

    只在设置里手动点“验证一下”时用，不做登录验身；分数是余弦相似度，
    不是概率；没注册时明确说没注册，不瞎猜。
    """
    from src.voice.voiceprint import decode_audio, verify

    principal = _require_self(context)
    data = await file.read()
    try:
        samples = decode_audio(data, file.filename or "recording.wav")
    except EngineUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail={"message": exc.detail, "install_hint": exc.install_hint},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        return verify(principal, samples)
    except EngineUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail={"message": exc.detail, "install_hint": exc.install_hint},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/voiceprint/calibrate")
def voiceprint_calibrate_endpoint(
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """用已存的注册样本重算比对阈值（觉得认得不准时点一次）。"""
    from src.voice.voiceprint import calibrate

    principal = _require_self(context)
    try:
        return calibrate(principal)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/voiceprint/enroll")
def voiceprint_delete_endpoint(
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """删除自己的声纹注册：记录与密文一起删，真没了（没有回收站）。"""
    from src.voice.voiceprint import delete_enrollment

    principal = _require_self(context)
    return {"deleted": bool(delete_enrollment(principal))}


@router.get("/wake/status")
def wake_status_endpoint() -> Dict[str, Any]:
    """唤醒词“贾维斯”状态：KWS 模型接通情况、监听开关与最近唤醒事件（见 src/voice/wake.py）。"""
    from src.voice.wake import wake_status

    return wake_status()


@router.post("/wake/model/download")
def wake_model_download_endpoint() -> Dict[str, Any]:
    """开始下载 Vosk 小模型（约 44MB，联网一次；老路，唤醒已不再走 Vosk）。"""
    from src.voice.wake import start_small_model_download

    return start_small_model_download()


class WakeListeningBody(BaseModel):
    enabled: bool = Field(description="常驻监听开还是关（默认关；开要先有 KWS 模型）。")


@router.post("/wake/listening")
def wake_listening_endpoint(body: WakeListeningBody) -> Dict[str, Any]:
    """常驻监听开关：开要先有引擎与模型（没有就原样报缺什么，不瞎开）。"""
    from src.voice.wake import set_listening_enabled

    return set_listening_enabled(body.enabled)


@router.post("/wake/kws-model/download")
def wake_kws_model_download_endpoint() -> Dict[str, Any]:
    """开始下载 KWS 唤醒模型（约 32MB，联网一次；下完验大小与 SHA-256 再解压）。"""
    from src.voice.wake import start_kws_model_download

    return start_kws_model_download()


@router.get("/wake/kws-model/download")
def wake_kws_model_download_status_endpoint() -> Dict[str, Any]:
    """KWS 唤醒模型下载进度（不触发下载）。"""
    from src.voice.wake import kws_download_status

    return kws_download_status()


@router.post("/wake/check")
async def wake_check_endpoint(file: UploadFile = File(...)) -> Dict[str, Any]:
    """验证一下：这段录音里有没有“贾维斯”（设置里真人声验证用；不存音频）。

    没引擎/没模型时 503 并说清缺什么；录音太短/太轻/格式不对时 400 并说原因。
    """
    from src.voice.wake import check_recording

    data = await file.read()
    try:
        return check_recording(data, file.filename or "recording.wav")
    except EngineUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail={"message": exc.detail, "install_hint": exc.install_hint},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/turns")
def list_voice_turns(conversation_id: str = "") -> Dict[str, Any]:
    """查一个会话最近的语音轮次诊断记录（验收对表用）。"""
    from src.voice.turns import list_turns

    turns = list_turns(conversation_id)
    return {"turns": turns, "count": len(turns)}
