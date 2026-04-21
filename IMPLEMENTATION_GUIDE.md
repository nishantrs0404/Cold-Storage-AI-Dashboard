# AI-Powered Cold Storage IoT System
## End-to-End Implementation & Deployment Guide

This guide provides step-by-step instructions for running the complete hardware, backend, and dashboard systems. It also includes an extensive troubleshooting section for common errors.

---

### Phase 1: Hardware Setup (ESP32)

1. **Connect the ESP32**
   - Connect your ESP32 board to your laptop via a data-capable micro-USB/USB-C cable.
2. **Configure Network & IP**
   - Open `esp32/esp32_code.ino` in the Arduino IDE.
   - Update `ssid` and `password` to match your local WiFi network.
   - Update `server_ip` with your laptop's current IPv4 address (Find this using `ipconfig` in the terminal).
3. **Flash the Firmware**
   - Select the correct COM port and Board ("DOIT ESP32 DEVKIT V1" or similar) in the Arduino IDE.
   - Click **Upload**.
   - Once complete, open the **Serial Monitor** (set baud rate to `115200`). You should see it connect to WiFi and begin attempting HTTP POST requests.

---

### Phase 2: Backend Setup (FastAPI & Machine Learning)

The backend is responsible for receiving data from the ESP32, running real-time machine learning predictions using scikit-learn models, storing history in SQLite, and providing WebSockets to the frontend.

1. **Open the Terminal**
   - Open a terminal/PowerShell window inside the `backend` folder.
2. **Activate the Virtual Environment**
   - Run: `.\.venv\Scripts\activate`
   - You should see `(.venv)` in your terminal prompt.
3. **Install Dependencies** (First-time setup only)
   - Ensure you are using Python 3.12 (Python 3.14+ is experimental and causes `pydantic-core` C++ compilation failures).
   - Run: `pip install -r requirements.txt`
4. **Start the Server**
   - Run: `uvicorn main:app --reload --port 8000 --host 0.0.0.0`
   - The `--host 0.0.0.0` flag is critical; it allows the ESP32 (an external device on your network) to safely connect to your laptop.

---

### Phase 3: Accessing the Dashboard

1. Once the FastAPI server is running (`uvicorn` shows "Application startup complete"), the frontend is automatically hosted.
2. Open your web browser (Chrome, Edge, etc.).
3. Navigate to: `http://localhost:8000/` or `http://127.0.0.1:8000/`
4. You will see the beautiful glassmorphism dark-mode UI start plotting live data.

---

### Common Errors & Troubleshooting

#### 1. ESP32 shows `Connection Refused` or `HTTP -1`
- **Cause:** The ESP32 is unable to reach the laptop. This is usually caused by Windows Defender Firewall blocking port 8000.
- **Solution:** 
  1. Open "Windows Defender Firewall with Advanced Security".
  2. Click **Inbound Rules** -> **New Rule**.
  3. Select **Port** -> **TCP** -> Specific port: `8000`.
  4. Select **Allow the connection**. Save it with a name like "FastAPI IoT Server".

#### 2. ESP32 flashes `Flash overflow` or `Sketch too big`
- **Cause:** Machine Learning libraries like TensorFlow Lite (`ArduTFLite.h`) consume too much memory constraints on the ESP32.
- **Solution:** Do not run inference locally on the ESP32. Instead, use the updated `esp32_code.ino`, which relies completely on the highly-efficient REST API backend to process ML tasks.

#### 3. Backend shows `ModuleNotFoundError: No module named 'fastapi'`
- **Cause:** The virtual environment is either not activated or the dependencies were installed globally instead of in the project environment.
- **Solution:** Run `.\.venv\Scripts\activate` first, and then run `pip install -r requirements.txt`.

#### 4. Backend shows `<module 'numpy' has no attribute 'save'>`
- **Cause:** Due to a NumPy update, dictionaries can no longer be directly saved without allowing pickles, or using native dictionary storage methods.
- **Solution:** This has already been patched in `main.py` using `np.savez_compressed` and standard integer typecasting, but if it rears its head again, ensure you are storing standard arrays, not Python dictionaries into NumPy logs.

#### 5. User Interface has broken styles or chart scripts won't load
- **Cause:** FastAPI expects static assets (like CSS and JS files) to be explicitly routed. If the HTML references `style.css`, it will fail.
- **Solution:** The HTML files must reference assets using the statically mounted `/static/` prefix. Example: `<link rel="stylesheet" href="/static/style.css">`. This has already been corrected in `index.html`.

#### 6. Dashboard constantly switches between correct and wrong readings
- **Cause:** Your system previously used a `simulation_loop()` to generate fake data because the hardware wasn't ready. When the Hardware *was* ready, both were sending data simultaneously.
- **Solution:** Ensure the `simulation_loop()` initialization is commented out inside `backend/main.py`.

---

**System Architecture Summary**
- **Hardware:** ESP32 -> Sensordata -> HTTP POST -> `http://<laptop-ip>:8000/api/ingest`
- **Backend:** FastAPI -> Validates Pydantic schema -> Serializes for ML Pipeline -> Scikit-learn `.pkl` Predicts -> Broadcasts via WebSockets
- **Frontend:** HTML5/CSS3/Vanilla JS -> Chart.js -> Fetches REST history via HTTP & Maintains live connection via WebSockets (`ws://`)
