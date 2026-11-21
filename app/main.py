from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from app.database import engine, Base
from app.routers import transactions, clients, audit


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize database tables
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="Tenant Ledger Platform",
    description="Multi-tenant immutable financial ledger service for advisory firms",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(transactions.router)
app.include_router(clients.router)
app.include_router(audit.router)


@app.get("/health", tags=["System"])
def health_check():
    return {"status": "healthy", "service": "tenant-ledger", "version": "1.0.0"}
