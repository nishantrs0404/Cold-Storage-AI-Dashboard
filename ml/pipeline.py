"""
Cold Storage AI -- Unified ML Pipeline
=======================================
Single script: Train, Evaluate, and optionally convert to TFLite.
Run from the ml/ directory:  python pipeline.py

Outputs (written to ./output/):
  model.pkl       -- Best sklearn model (pickle)
  scaler.pkl      -- Fitted MinMaxScaler (pickle)
  results.json    -- Metrics + norm params + weights (consumed by backend)
  model_params.h  -- C header with scaler ranges (copied into esp32/)
  [model.tflite]  -- If TensorFlow is installed
  [model.h]       -- Auto-generated C array of TFLite binary
"""
import os
import sys
import json
import pickle
import logging

import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.preprocessing import MinMaxScaler
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.metrics import (
    accuracy_score, precision_score, recall_score,
    f1_score, roc_auc_score, confusion_matrix,
)

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

# ── Paths ──────────────────────────────────────────────────────────────────────
_here    = os.path.dirname(os.path.abspath(__file__))          # ml/
_esp32   = os.path.join(_here, "..", "esp32")                  # esp32/
DATASET  = os.getenv("DATASET_PATH", os.path.join(_here, "dataset.csv"))
OUT      = os.getenv("OUTPUT_DIR",   os.path.join(_here, "output"))
os.makedirs(OUT, exist_ok=True)

# ── Constants ──────────────────────────────────────────────────────────────────
FEATURES = ["temperature", "humidity", "mq2", "mq135"]
LABEL    = "label"

# ==============================================================================
#  STEP 1: LOAD + VALIDATE DATA
# ==============================================================================
log.info("=" * 60)
log.info("COLD STORAGE AI -- UNIFIED ML PIPELINE")
log.info("=" * 60)

# FIX #1 (was: crash on missing file with no message)
if not os.path.exists(DATASET):
    log.error("Dataset not found: %s", DATASET)
    log.error("Place dataset.csv inside the ml/ folder and retry.")
    sys.exit(1)

df = pd.read_csv(DATASET)

# FIX #2 (was: KeyError crash if columns missing)
missing_cols = [c for c in FEATURES + [LABEL] if c not in df.columns]
if missing_cols:
    log.error("Dataset is missing required columns: %s", missing_cols)
    log.error("Available columns: %s", list(df.columns))
    sys.exit(1)

# Drop NaN rows and reset index
df.dropna(subset=FEATURES + [LABEL], inplace=True)
df.reset_index(drop=True, inplace=True)

X = df[FEATURES].values.astype(float)
y = df[LABEL].values.astype(int)

log.info("Dataset: %s | %d rows | Class distribution: %s",
         DATASET, len(df), dict(zip(*np.unique(y, return_counts=True))))

# Guard: need at least 2 classes to do classification
if len(np.unique(y)) < 2:
    log.error("Dataset has only one class label — cannot train a classifier.")
    sys.exit(1)

# ==============================================================================
#  STEP 2: PREPROCESS
# ==============================================================================
scaler  = MinMaxScaler()
X_s     = scaler.fit_transform(X)
X_train, X_test, y_train, y_test = train_test_split(
    X_s, y, test_size=0.3, random_state=42, stratify=y
)

# ==============================================================================
#  STEP 3: TRAIN + EVALUATE
# ==============================================================================
model_defs = {
    "LogisticRegression": LogisticRegression(max_iter=1000, random_state=42),
    "RandomForest":       RandomForestClassifier(n_estimators=100, random_state=42, n_jobs=-1),
    "GradientBoosting":   GradientBoostingClassifier(n_estimators=100, random_state=42),
}

results: dict = {}
best_name, best_model, best_auc = "", None, -1.0

for name, clf in model_defs.items():
    log.info("Training %s ...", name)
    clf.fit(X_train, y_train)
    y_pred = clf.predict(X_test)
    y_prob = clf.predict_proba(X_test)[:, 1]

    auc = roc_auc_score(y_test, y_prob)
    cv  = cross_val_score(clf, X_s, y, cv=5, scoring="accuracy", n_jobs=-1)

    # FIX #3 — confusion_matrix().ravel() crashes when only 1 class in test split.
    # Use labels= to force all 4 values to exist.
    cm = confusion_matrix(y_test, y_pred, labels=[0, 1])
    tn, fp, fn, tp = cm.ravel()

    results[name] = {
        "accuracy":  round(float(accuracy_score(y_test, y_pred)), 4),
        "precision": round(float(precision_score(y_test, y_pred, zero_division=0)), 4),
        "recall":    round(float(recall_score(y_test, y_pred, zero_division=0)), 4),
        "f1":        round(float(f1_score(y_test, y_pred, zero_division=0)), 4),
        "auc":       round(float(auc), 4),
        "cv_mean":   round(float(cv.mean()), 4),
        "cv_std":    round(float(cv.std()), 4),
        "confusion": {"TP": int(tp), "FP": int(fp), "FN": int(fn), "TN": int(tn)},
    }

    log.info("  %s | AUC=%.4f | Acc=%.4f | F1=%.4f | CV=%.4f±%.4f",
             name, auc, results[name]["accuracy"], results[name]["f1"],
             cv.mean(), cv.std())
    log.info("  TP=%d FP=%d FN=%d TN=%d", tp, fp, fn, tn)

    if auc > best_auc:
        best_auc, best_name, best_model = auc, name, clf

log.info("=" * 60)
log.info("BEST: %s (AUC=%.4f)", best_name, best_auc)
log.info("=" * 60)

# FIX #4 — guard for pathological case where all AUCs were 0 (shouldn't happen
#           but prevents NoneType crash on the attribute access below).
if best_model is None:
    log.error("No model was trained successfully. Exiting.")
    sys.exit(1)

# ==============================================================================
#  STEP 4: SAVE ARTIFACTS
# ==============================================================================
# -- 4a: Pickle best model + scaler -------------------------------------------
with open(os.path.join(OUT, "model.pkl"),  "wb") as f: pickle.dump(best_model, f)
with open(os.path.join(OUT, "scaler.pkl"), "wb") as f: pickle.dump(scaler,     f)

# -- 4b: Extract weights for backend logistic-regression inference  ------------
#        (Only Logistic Regression has coef_ / intercept_; tree models don't.)
lr_weights: list[float] = []
lr_bias:    float       = 0.0

if hasattr(best_model, "coef_"):
    lr_weights = best_model.coef_[0].tolist()
    lr_bias    = float(best_model.intercept_[0])
    # FIX #5 — was: np.save(path, dict) which is undefined behaviour.
    #           np.save is for ndarrays; use np.savez_compressed for multiple arrays.
    np.savez_compressed(
        os.path.join(OUT, "weights.npz"),
        weights=best_model.coef_[0],
        bias=np.array([best_model.intercept_[0]])
    )
    log.info("Saved weights.npz (%d coefficients)", len(lr_weights))

# -- 4c: Scaler C header for ESP32 (absolute path) ----------------------------
params_h_path = os.path.join(OUT, "model_params.h")
with open(params_h_path, "w") as f:
    f.write("// Auto-generated — do not edit. Run pipeline.py to regenerate.\n")
    f.write(f"// Best model: {best_name}  AUC={best_auc:.4f}\n\n")
    f.write(f"float min_vals[4] = {{{', '.join(f'{v:.8f}f' for v in scaler.data_min_)}}};\n")
    f.write(f"float max_vals[4] = {{{', '.join(f'{v:.8f}f' for v in scaler.data_max_)}}};\n")
log.info("Saved model_params.h")

# -- 4d: results.json (consumed by backend's load_ml_model()) -----------------
results_path = os.path.join(OUT, "results.json")
with open(results_path, "w") as f:
    json.dump({
        "best":    best_name,
        "auc":     round(best_auc, 4),
        "results": results,
        # These two keys are read by backend/main.py → load_ml_model()
        "weights": lr_weights,
        "bias":    lr_bias,
        "norm": {
            "min": scaler.data_min_.tolist(),
            "max": scaler.data_max_.tolist(),
        },
    }, f, indent=2)
log.info("Saved results.json")

log.info("\nSaved artifacts: model.pkl, scaler.pkl, weights.npz, model_params.h, results.json")

# ==============================================================================
#  STEP 5: TFLITE CONVERSION (optional — requires tensorflow)
# ==============================================================================
try:
    import tensorflow as tf  # type: ignore[import]

    nn = tf.keras.Sequential([
        tf.keras.layers.Dense(16, activation="relu", input_shape=(4,)),
        tf.keras.layers.Dense(8,  activation="relu"),
        tf.keras.layers.Dense(1,  activation="sigmoid"),
    ])
    nn.compile(optimizer="adam", loss="binary_crossentropy", metrics=["accuracy"])
    # Use best_model predictions as soft labels for knowledge distillation
    nn.fit(X_s, best_model.predict(X_s).astype(float),
           epochs=50, batch_size=32, verbose=0)

    converter = tf.lite.TFLiteConverter.from_keras_model(nn)
    tflite_bytes = converter.convert()

    # Save raw binary
    tflite_path = os.path.join(OUT, "model.tflite")
    with open(tflite_path, "wb") as f:
        f.write(tflite_bytes)

    # FIX #6 — was: relative path "../esp32/model.h" which breaks if
    #           the script is run from a directory other than ml/.
    #           Now uses an absolute path computed from __file__.
    model_h_path = os.path.join(_esp32, "model.h")
    hex_str = ", ".join(f"0x{b:02x}" for b in tflite_bytes)
    with open(model_h_path, "w") as f:
        f.write("#ifndef MODEL_H\n#define MODEL_H\n\n")
        f.write(f"// Auto-generated TFLite model — {len(tflite_bytes)} bytes\n")
        f.write("alignas(8) const unsigned char model[] = {\n    ")
        f.write(hex_str)
        f.write("\n};\n\n")
        f.write(f"const unsigned int model_len = {len(tflite_bytes)};\n\n")
        f.write("#endif // MODEL_H\n")

    log.info("TFLite saved: %d bytes → %s", len(tflite_bytes), tflite_path)
    log.info("model.h exported → %s", model_h_path)

except ImportError:
    log.info("[INFO] TensorFlow not installed — skipping TFLite conversion.")
    log.info("       Install with: pip install tensorflow")
except Exception as exc:
    log.warning("[WARN] TFLite conversion failed: %s", exc)

log.info("\n[DONE] Pipeline complete.")
