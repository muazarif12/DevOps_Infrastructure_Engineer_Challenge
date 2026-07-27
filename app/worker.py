"""LiveKit worker entrypoint.

Boots the real-time voice pipeline and starts the intake at the Welcome stage. The stages
themselves live in ``app.flow``; this module only assembles the models and session.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from livekit.agents import (
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    cli,
    inference,
    room_io,
)
from livekit.plugins import ai_coustics, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel

from app.flow import IntakeState, WelcomeStage
from app.store import create_schema

# Load credentials from the .env.local next to the project root, regardless of the
# directory the worker is launched from.
load_dotenv(Path(__file__).resolve().parent.parent / ".env.local")

log = logging.getLogger("intake-worker")

# Infrastructure binding: the LiveKit SIP dispatch rule for the provisioned number targets
# this exact agent name, so keeping it lets the existing phone number route here unchanged.
DISPATCH_AGENT_NAME = "my-agent"

# Model selection via LiveKit Inference (no separate provider keys needed). These ids follow
# the "provider/model" convention; check the LiveKit Inference catalog to swap them.
TRANSCRIBER = "deepgram/nova-3"
BRAIN = "openai/gpt-4.1"
VOICE_MODEL = "cartesia/sonic-3"
VOICE_ID = "9626c31c-bec5-4cca-baa8-f8ba9e84c8bc"

# Prometheus metrics the SDK ships but doesn't expose unless asked: worker load, active job
# count, child-process count, process-init duration (see livekit.agents.telemetry.metrics).
# multiproc_dir is required for the gauges to aggregate correctly across the prewarmed job
# subprocesses, not just the main worker process.
_PROM_MULTIPROC_DIR = os.environ.get("PROMETHEUS_MULTIPROC_DIR", "/tmp/lk_prometheus_multiproc")

server = AgentServer(
    # Cloud Run injects PORT and health-checks the container on it — the worker doesn't serve
    # real traffic there (it's egress-only to LiveKit Cloud), but the container still needs to
    # answer on whatever port Cloud Run expects or the revision never becomes ready. Defaults to
    # 8081 (the SDK's own default) everywhere else, since PORT is unset on Railway/Compose/bare
    # `uv run`.
    port=int(os.environ.get("PORT", "8081")),
    prometheus_port=int(os.environ.get("WORKER_PROMETHEUS_PORT", "9091")),
    prometheus_multiproc_dir=_PROM_MULTIPROC_DIR,
)


def _load_models(proc: JobProcess) -> None:
    # Load the voice-activity model once per worker process and reuse it across calls.
    proc.userdata["vad"] = silero.VAD.load()
    # Ensure the schema exists once per prewarmed process, not on every inbound call. Against
    # Postgres, create_all() still does a catalog round-trip per call it's asked to run in —
    # cheap once at process startup, wasteful (and needlessly racy under concurrent calls)
    # repeated on every job.
    create_schema()


server.setup_fnc = _load_models


@server.rtc_session(agent_name=DISPATCH_AGENT_NAME)
async def handle_call(ctx: JobContext) -> None:
    ctx.log_context_fields = {"room": ctx.room.name}

    session = AgentSession[IntakeState](
        userdata=IntakeState(),
        stt=inference.STT(model=TRANSCRIBER, language="multi"),
        llm=inference.LLM(model=BRAIN),
        tts=inference.TTS(model=VOICE_MODEL, voice=VOICE_ID),
        turn_detection=MultilingualModel(),
        vad=ctx.proc.userdata["vad"],
        # Start drafting a reply before the caller's turn is fully finished, to cut latency.
        preemptive_generation=True,
    )

    await ctx.connect()

    # The Welcome stage greets on entry, so no separate opening prompt is needed here.
    await session.start(
        agent=WelcomeStage(),
        room=ctx.room,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=ai_coustics.audio_enhancement(
                    model=ai_coustics.EnhancerModel.QUAIL_VF_L
                ),
            ),
        ),
    )


if __name__ == "__main__":
    cli.run_app(server)
