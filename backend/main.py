"""
Cold Storage AI Monitor -- Unified FastAPI Backend
====================================================
Single-file backend: REST API + WebSocket + Analytics + ML Inference.
ESP32 posts sensor data directly via HTTP. No Firebase needed.
Frontend served as static files from the same server.
"""
import os
import math
import json
import random
import asyncio
import logging
import statistics
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from collections import deque

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)


# ==============================================================
#  DATA STORE (SQLite Database + Mem Cache)
# ==============================================================
MAX_RECORDS = 3600  # 120 min cache for fast API analytics
data_store: deque[dict] = deque(maxlen=MAX_RECORDS)
ws_clients: list[WebSocket] = []

try:
    from database import SessionLocal, SensorRecord
    db_enabled = True
    print("[INIT] Database persistence connected.")
except ImportError:
    db_enabled = False
    print("[INIT] No database file found, running purely in-memory.")

def push_to_db(record: dict, pred: dict):
    if not db_enabled:
        return
    db = SessionLocal()
    try:
        db_record = SensorRecord(
            timestamp=record["timestamp"],
            temperature=record["temperature"],
            humidity=record["humidity"],
            mq2=record["mq2"],
            mq135=record["mq135"],
            status=pred["status"],
            probability=pred["probability"],
            confidence=pred["confidence"]
        )
        db.add(db_record)
        db.commit()
    except Exception as e:
        print(f"DB Insert failed: {e}")
    finally:
        db.close()


def seed_simulated_data():
    """Pre-fill 30 min of realistic simulated sensor data."""
    now = datetime.now(timezone.utc)
    for i in range(900):
        t = now - timedelta(seconds=(900 - i) * 2)
        danger_period = (i % 120) > 90
        cycle = math.sin(i * 0.02) * 0.3
        if danger_period:
            temp  = round(float(random.gauss(9.5, 2.5) + cycle), 2)
            hum   = round(float(random.gauss(62.0, 8.0)), 2)
            mq2   = round(float(random.gauss(850.0, 200.0)), 2)
            mq135 = round(float(random.gauss(820.0, 190.0)), 2)
        else:
            temp  = round(float(random.gauss(3.5, 1.5) + cycle), 2)
            hum   = round(float(random.gauss(87.0, 5.0)), 2)
            mq2   = round(float(random.gauss(250.0, 80.0)), 2)
            mq135 = round(float(random.gauss(230.0, 70.0)), 2)
        data_store.append({
            "temperature": max(-5.0, min(20.0, temp)),
            "humidity":    max(30.0, min(100.0, hum)),
            "mq2":         max(50.0, min(2200.0, mq2)),
            "mq135":       max(50.0, min(2200.0, mq135)),
            "timestamp":   t.isoformat(),
        })


# ==============================================================
#  ML MODEL (Dynamic Loading)
# ==============================================================
API_KEY = os.getenv("IOT_API_KEY", "super-secret-edge-key-2026")
MODEL_OUTPUT_DIR = os.getenv("MODEL_OUTPUT_DIR", "../ml/output")

MIN_VALS = [-2.0, 35.1162184, 50.0, 50.0]
MAX_VALS = [16.73054198, 100.0, 2111.40921797, 2082.26308246]
WEIGHTS = [0.8, -0.6, 0.5, 0.45]
BIAS = -0.3

def load_ml_model():
    """Reads the JSON output natively from the ML training script."""
    global MIN_VALS, MAX_VALS, WEIGHTS, BIAS
    json_path = os.path.join(os.path.dirname(__file__), MODEL_OUTPUT_DIR, "results.json")
    if os.path.exists(json_path):
        try:
            with open(json_path, "r") as f:
                data = json.load(f)
            MIN_VALS = data["norm"]["min"]
            MAX_VALS = data["norm"]["max"]
            WEIGHTS = data.get("weights", WEIGHTS)
            BIAS = data.get("bias", BIAS)
            print("[INIT] Dynamically loaded ML parameters.")
        except Exception as e:
            print(f"[WARN] Could not load ML dynamic config: {e}")

load_ml_model()


def predict(sensor: dict) -> dict:
    """Run inference -- identical logic to ESP32 TinyML model using pure Python."""
    raw = [sensor["temperature"], sensor["humidity"], sensor["mq2"], sensor["mq135"]]
    
    # Normalize (MinMax)
    normed = []
    for i in range(4):
        # Add tiny epsilon to avoid division by zero
        n = (raw[i] - MIN_VALS[i]) / ((MAX_VALS[i] - MIN_VALS[i]) + 1e-8)
        normed.append(max(0.0, min(1.0, n)))
        
    # Logistic Regression Math
    dot_product = sum(WEIGHTS[i] * normed[i] for i in range(4)) + BIAS
    
    try:
        prob = 1.0 / (1.0 + math.exp(-dot_product))
    except OverflowError:
        prob = 0.0 if dot_product < 0 else 1.0
        
    danger = bool(prob > 0.5)
    return {
        "status": "DANGER" if danger else "SAFE",
        "probability": round(float(prob), 4),
        "confidence": round(float(max(prob, 1 - prob)) * 100, 1),
        "label": int(danger),
    }


# ==============================================================
#  ANALYTICS ENGINE (inline)
# ==============================================================
SENSORS = ["temperature", "humidity", "mq2", "mq135"]
THRESHOLDS = {
    "temperature": (8.0, 12.0),   # safe_max, critical_max
    "humidity": (65.0, 50.0),     # safe_min, critical_min (inverted)
    "mq2": (600.0, 1000.0),
    "mq135": (600.0, 1000.0),
}


def compute_stats(data: list[dict]) -> dict:
    if not data:
        return {}
    result = {}
    for key in SENSORS:
        vals = [d[key] for d in data if key in d]
        if not vals:
            continue
        result[key] = {
            "mean": round(statistics.mean(vals), 2),
            "median": round(statistics.median(vals), 2),
            "std": round(statistics.stdev(vals), 2) if len(vals) > 1 else 0,
            "min": round(min(vals), 2),
            "max": round(max(vals), 2),
        }
    return result


def compute_trends(data: list[dict]) -> dict:
    if len(data) < 5:
        return {k: "insufficient_data" for k in SENSORS}
    result = {}
    for key in SENSORS:
        vals = [d[key] for d in data]
        n = len(vals)
        x_mean = (n - 1) / 2
        y_mean = statistics.mean(vals)
        num = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(vals))
        den = sum((i - x_mean) ** 2 for i in range(n))
        slope = num / den if den else 0
        result[key] = "increasing" if slope > 0.01 else "decreasing" if slope < -0.01 else "stable"
    return result


def compute_risk(latest: dict) -> dict:
    score, reasons = 0, []
    t, h = latest.get("temperature", 0), latest.get("humidity", 100)
    m2, m135 = latest.get("mq2", 0), latest.get("mq135", 0)

    if t > 12: score += 30; reasons.append(f"Temp CRITICAL ({t:.1f}C)")
    elif t > 8: score += 15; reasons.append(f"Temp HIGH ({t:.1f}C)")
    if h < 50: score += 25; reasons.append(f"Humidity CRITICAL ({h:.1f}%)")
    elif h < 65: score += 12; reasons.append(f"Humidity LOW ({h:.1f}%)")
    if m2 > 1000: score += 25; reasons.append(f"MQ2 CRITICAL ({m2:.0f})")
    elif m2 > 600: score += 12; reasons.append(f"MQ2 HIGH ({m2:.0f})")
    if m135 > 1000: score += 20; reasons.append(f"MQ135 CRITICAL ({m135:.0f})")
    elif m135 > 600: score += 10; reasons.append(f"MQ135 HIGH ({m135:.0f})")

    level = "CRITICAL" if score >= 50 else "HIGH" if score >= 30 else "MODERATE" if score >= 15 else "LOW"
    return {"level": level, "score": min(score, 100),
            "message": "; ".join(reasons) or "All sensors within safe range"}


def get_history(minutes: int) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    result = []
    for r in data_store:
        try:
            ts = datetime.fromisoformat(r["timestamp"])
            # FIX: ensure timezone-aware comparison (isoformat with 'Z' or '+00:00')
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts >= cutoff:
                result.append(r)
        except (ValueError, KeyError):
            pass  # Skip malformed records
    return result


# ==============================================================
#  FASTAPI LIFESPAN (must be defined BEFORE app = FastAPI(...))
# ==============================================================
@asynccontextmanager
async def lifespan(application: FastAPI):
    """Application startup and shutdown lifecycle."""
    # --- STARTUP ---
    if db_enabled:
        db = SessionLocal()
        try:
            records = (
                db.query(SensorRecord)
                .order_by(SensorRecord.id.desc())
                .limit(MAX_RECORDS)
                .all()
            )
            for r in reversed(records):
                data_store.append({
                    "timestamp":   r.timestamp,
                    "temperature": r.temperature,
                    "humidity":    r.humidity,
                    "mq2":         r.mq2,
                    "mq135":       r.mq135,
                })
            log.info("[STARTUP] Loaded %d records from database.", len(data_store))
        except Exception as exc:
            log.warning("[STARTUP] DB load failed: %s", exc)
        finally:
            db.close()

    if not data_store:
        seed_simulated_data()
        log.info("[STARTUP] No DB records — seeded with %d simulated readings.", len(data_store))

    # sim_task = asyncio.create_task(simulation_loop())
    log.info("[STARTUP] Hardware mode enabled. Simulator disabled.")

    yield  # Application runs here

    # --- SHUTDOWN ---
    # sim_task.cancel()
    log.info("[SHUTDOWN] Server stopped.")


# ==============================================================
#  FASTAPI APPLICATION
# ==============================================================
app = FastAPI(title="Cold Storage AI Monitor", version="2.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# Serve frontend static files
frontend_dir = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.isdir(frontend_dir):
    app.mount("/static", StaticFiles(directory=frontend_dir), name="static")


async def simulation_loop():
    """Continuously generate simulated data + broadcast via WebSocket."""
    idx = len(data_store)
    while True:
        await asyncio.sleep(2)
        idx += 1
        danger_period = (idx % 120) > 90
        cycle = math.sin(idx * 0.02) * 0.3
        ts = datetime.now(timezone.utc).isoformat()
        if danger_period:
            reading: dict = {
                "temperature": round(float(max(-5.0, min(20.0, random.gauss(9.5, 2.5) + cycle))), 2),
                "humidity":    round(float(max(30.0, min(100.0, random.gauss(62.0, 8.0)))), 2),
                "mq2":         round(float(max(50.0, min(2200.0, random.gauss(850.0, 200.0)))), 2),
                "mq135":       round(float(max(50.0, min(2200.0, random.gauss(820.0, 190.0)))), 2),
                "timestamp":   ts,
            }
        else:
            reading = {
                "temperature": round(float(max(-5.0, min(20.0, random.gauss(3.5, 1.5) + cycle))), 2),
                "humidity":    round(float(max(30.0, min(100.0, random.gauss(87.0, 5.0)))), 2),
                "mq2":         round(float(max(50.0, min(2200.0, random.gauss(250.0, 80.0)))), 2),
                "mq135":       round(float(max(50.0, min(2200.0, random.gauss(230.0, 70.0)))), 2),
                "timestamp":   ts,
            }
        pred = predict(reading)

        # Save to DB and cache
        push_to_db(reading, pred)
        data_store.append(reading)
        
        # Broadcast to all WebSocket clients
        payload = {"timestamp": reading["timestamp"], "sensors": reading, "prediction": pred}
        for ws in ws_clients[:]:
            try:
                await ws.send_json(payload)
            except Exception:
                ws_clients.remove(ws)


# ---------- ROUTES ----------

@app.get("/")
async def index():
    f = os.path.join(frontend_dir, "index.html")
    if os.path.exists(f):
        return FileResponse(f)
    return {"service": "Cold Storage AI Monitor", "version": "2.0.0"}


@app.get("/health")
async def health():
    return {"status": "healthy", "records": len(data_store),
            "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/api/latest")
async def api_latest():
    if not data_store:
        return {"error": "No data"}
    latest = data_store[-1]
    return {"sensors": latest, "prediction": predict(latest),
            "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/api/history")
async def api_history(minutes: int = Query(10, ge=1, le=120)):
    data = get_history(minutes)
    return {"minutes": minutes, "count": len(data), "data": data,
            "stats": compute_stats(data)}


@app.get("/api/analytics")
async def api_analytics():
    d10, d30 = get_history(10), get_history(30)
    latest = data_store[-1] if data_store else {}
    return {"short_term": compute_stats(d10), "long_term": compute_stats(d30),
            "trends": compute_trends(d30), "risk_level": compute_risk(latest)}


@app.get("/api/status")
async def api_status():
    if not data_store:
        return {"status": "UNKNOWN", "confidence": 0}
    latest = data_store[-1]
    pred = predict(latest)
    risk = compute_risk(latest)
    return {"status": pred["status"], "confidence": pred["confidence"],
            "risk_level": risk["level"], "risk_score": risk["score"],
            "message": risk["message"]}


class SensorInput(BaseModel):
    temperature: float
    humidity: float
    mq2: float
    mq135: float


@app.post("/api/predict")
async def api_predict(data: SensorInput):
    # FIX: .dict() is deprecated in Pydantic v2 — use .model_dump()
    payload = data.model_dump()
    return {"input": payload, "prediction": predict(payload)}


@app.post("/api/ingest")
async def api_ingest(data: SensorInput, x_api_key: str = Header(None)):
    """ESP32 posts sensor data here directly. Secured by edge API Key."""
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing IoT API Key")
    # FIX: .dict() is deprecated in Pydantic v2 — use .model_dump()
    reading = data.model_dump()
    reading["timestamp"] = datetime.now(timezone.utc).isoformat()
    pred = predict(reading)

    push_to_db(reading, pred)
    data_store.append(reading)

    # Broadcast to WebSocket clients — iterate over a snapshot
    dead_clients = []
    broadcast = {"timestamp": reading["timestamp"], "sensors": reading, "prediction": pred}
    for ws in ws_clients[:]:
        try:
            await ws.send_json(broadcast)
        except Exception:
            dead_clients.append(ws)
    for ws in dead_clients:
        ws_clients.remove(ws)

    return {"status": "ok", "prediction": pred}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    ws_clients.append(ws)
    try:
        while True:
            await ws.receive_text()  # Keep alive
    except WebSocketDisconnect:
        ws_clients.remove(ws)
