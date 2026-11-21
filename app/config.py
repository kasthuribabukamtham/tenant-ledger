from pydantic import BaseModel
import os

class Settings(BaseModel):
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./tenant_ledger.db")
    SUPPORTED_CURRENCIES: set[str] = {"USD", "EUR", "GBP", "CAD", "AUD", "JPY", "CHF"}
    MAX_BULK_BATCH_SIZE: int = 5000
    DUPLICATE_WINDOW_SECONDS: int = 300  # 5 minutes window for duplicate heuristic

settings = Settings()
