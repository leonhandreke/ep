from fastapi import FastAPI

from ep.api import router

app = FastAPI(title="EP")
app.include_router(router)
