"""Server production nhỏ: account/credit + CapCut TTS cùng cổng 8007."""
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from account_api import migrate, router as account_router
from tts_server import app as tts_app


@asynccontextmanager
async def lifespan(_app: FastAPI):
    migrate()
    yield


# Tái sử dụng app TTS để giữ nguyên toàn bộ endpoint /voices, /tts, /audio.
tts_app.router.lifespan_context = lifespan
tts_app.include_router(account_router)
tts_app.title = "Threads Desktop API"
app = tts_app


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=os.getenv("API_HOST", "0.0.0.0"), port=int(os.getenv("API_PORT", "8007")))
