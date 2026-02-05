# main.py

from fastapi import FastAPI

from database import engine
from models import Base

from auth import router as auth_router
from device import router as device_router
from memory import router as memory_router
from eggbook import router as eggbook_router




app = FastAPI(
    title="Egg Backend",
    version="1.0"
)


# 注册路由
app.include_router(auth_router)
app.include_router(device_router)
app.include_router(memory_router)
app.include_router(eggbook_router)


@app.get("/")
def health_check():

    return {
        "status": "ok",
        "service": "Egg Backend"
    }

