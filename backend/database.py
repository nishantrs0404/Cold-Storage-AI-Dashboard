"""
database.py -- SQLAlchemy ORM for Cold Storage AI Monitor
==========================================================
Manages persistent SQLite storage for sensor readings.

Call graph:
  main.py -> from database import SessionLocal, SensorRecord
  main.py startup -> db.query(SensorRecord) to prefill cache
  main.py api_ingest -> push_to_db() -> db.add(SensorRecord)
"""
import os
import logging
from sqlalchemy import create_engine, Column, Integer, Float, String, text
from sqlalchemy.orm import declarative_base, sessionmaker
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# ── Load .env from the project root (two levels up from backend/) ──────────────
_this_dir = os.path.dirname(os.path.abspath(__file__))
_env_path  = os.path.join(_this_dir, "..", ".env")
load_dotenv(dotenv_path=_env_path)

# ── Database configuration ─────────────────────────────────────────────────────
# Default: store DB file next to this module (inside backend/).
# Override by setting DATABASE_URL in .env e.g. sqlite:////abs/path/data.db
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{os.path.join(_this_dir, 'cold_storage.db')}")

# ── Engine + session factory ───────────────────────────────────────────────────
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},  # required for SQLite + FastAPI threads
    echo=False,                                   # set True to log SQL queries
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# ── ORM Model ─────────────────────────────────────────────────────────────────
class SensorRecord(Base):
    """One row = one sensor reading with ML inference result."""
    __tablename__ = "sensor_data"

    id          = Column(Integer, primary_key=True, index=True)
    timestamp   = Column(String,  nullable=False, index=True)
    temperature = Column(Float,   nullable=False)
    humidity    = Column(Float,   nullable=False)
    mq2         = Column(Float,   nullable=False)
    mq135       = Column(Float,   nullable=False)
    status      = Column(String,  nullable=False, default="UNKNOWN")
    probability = Column(Float,   nullable=False, default=0.0)
    confidence  = Column(Float,   nullable=False, default=0.0)

    def to_dict(self) -> dict:
        """Convert row to plain dict — used by API endpoints."""
        return {
            "id":          self.id,
            "timestamp":   self.timestamp,
            "temperature": self.temperature,
            "humidity":    self.humidity,
            "mq2":         self.mq2,
            "mq135":       self.mq135,
            "status":      self.status,
            "probability": self.probability,
            "confidence":  self.confidence,
        }


# ── Create tables (safe: no-op if already exists) ─────────────────────────────
# Wrapped in a try/except so an inaccessible DB path does NOT crash
# the entire backend — main.py catches ImportError and falls back to in-memory.
try:
    Base.metadata.create_all(bind=engine)
    # Verify connection is actually alive
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    logger.info("[DB] SQLite connected: %s", DATABASE_URL)
except Exception:
    logger.error("[DB] Failed to initialise database: %s", DATABASE_URL, exc_info=True)
    raise  # Re-raise so main.py's try/except ImportError catches it gracefully
