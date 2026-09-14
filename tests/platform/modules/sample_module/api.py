from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class Ping(BaseModel):
    module: str
    reply: str


class Echo(BaseModel):
    text: str


@router.get("/ping", response_model=Ping)
def ping() -> Ping:
    return Ping(module="hello", reply="pong")


@router.post("/echo", response_model=Echo)
def echo(body: Echo) -> Echo:
    return body
