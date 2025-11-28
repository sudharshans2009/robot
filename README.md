# Robotics WebSocket System - Refactored

## Overview
This project is a robotics system with three main components:
1. **Hand Controller** – MicroPython on Raspberry Pi Pico WH (wearable sensor glove)
2. **Mech Controller** – MicroPython on Raspberry Pi Pico WH (robotic arm)
3. **Node.js Server** – Central WebSocket router and control hub

## Project Structure

```
robot/
├── hand/
│   ├── main_ws.py              # Original hand controller
│   └── main_refactored.py      # NEW: Refactored hand controller
├── mech/
│   ├── main_ws.py              # Original mech controller
│   └── main_refactored.py      # NEW: Refactored mech controller
├── server/
│   ├── server.js               # Original server
│   ├── server_new.js           # NEW: Refactored server
│   └── public/
│       ├── index.html          # Original home page
│       ├── index_new.html      # NEW: Refactored home page
│       ├── admin.html          # Original admin page
│       └── admin_new.html      # NEW: Refactored admin page
└── README.md
```

---

## Quick Start

### 1. Start the Server
```bash
cd server
npm install
node server_new.js
```

Server runs on:
- **HTTP**: http://localhost:3001
- **WebSocket**: ws://localhost:8081

### 2. Access Web Interfaces
- **Home Page**: http://localhost:3001/index_new.html
- **Admin Page**: http://localhost:3001/admin_new.html

### 3. Deploy Controllers
On each Pico WH, rename the refactored file to `main.py`:
- Hand: `main_refactored.py` → `main.py`
- Mech: `main_refactored.py` → `main.py`

Update WiFi and server IP in the configuration section of each file.

---

## Unified Protocol

Every message follows this envelope:

```jsonc
{
  "type": "<category>.<action>",
  "source": "hand" | "mech" | "site" | "admin" | "server",
  "target": "server" | "hand" | "mech" | "site" | "admin" | "broadcast",
  "payload": { /* action-specific data */ },
  "timestamp": 1234567890
}
```

### Message Types

| Type | Direction | Payload |
|------|-----------|---------|
| `hand.flex_data` | hand → server | `{ flex_1_2, flex_3_4, flex_5 }` (0–100%) |
| `hand.biometric_data` | hand → server | `{ heart_rate, spo2, status, red, ir }` |
| `hand.emergency_manual` | hand → server | `{ active: boolean }` |
| `mech.sensor_data` | mech → server | `{ servos, gas, temperature, distance }` |
| `server.control_servo` | server → mech | `{ servo_id, angle }` |
| `server.control_servos` | server → mech | `{ angles: [s1, s2, s3, s4, s5] }` |
| `emergency.status` | server → broadcast | `{ active, level, autoTriggered, manualTriggered }` |
| `admin.override_state` | admin → server | Full override configuration |

---

## Hardware Configuration

### Hand Controller (Pico WH)
| Component | Pin |
|-----------|-----|
| Flex 1-2 (thumb/index) | GP28 |
| Flex 3-4 (middle/ring) | GP27 |
| Flex 5 (pinky) | GP26 |
| MAX30102 SDA | GP6 |
| MAX30102 SCL | GP7 |
| Emergency Button | GP21 |
| Status LED | GP20 |

### Mech Controller (Pico WH)
| Component | Pin |
|-----------|-----|
| Servo 1 (thumb) | GP10 |
| Servo 2 (index) | GP11 |
| Servo 3 (middle) | GP12 |
| Servo 4 (ring) | GP14 |
| Servo 5 (pinky) | GP15 |
| Gas Sensor (MQ-2) | GP26 |
| Temperature (DS18B20) | GP27 |
| Ultrasonic TRIG | GP20 |
| Ultrasonic ECHO | GP19 |

---

## Features

### Flex → Servo Control
- **Direct Mode**: Flex sensor data is displayed on home page
- **Forward to Servos**: When enabled in admin, flex values are converted to servo angles
- **Conversion**: `angle = (flex_percentage / 100) × 180`

### Emergency System
**Auto-Triggered** when:
- Heart Rate < 50 or > 150 BPM
- SpO2 < 90%
- Gas > 150 PPM
- Temperature > 45°C

**Manual Trigger**: Press emergency button on hand controller or use home/admin page

**During Emergency**:
- All servos lock to 90°
- Visual alerts on all web pages
- Status broadcast to all clients

### Override System (Admin)
- **Flex Override**: Override individual sensor values, optionally forward to servos
- **Biometric Override**: Set test HR/SpO2 values
- **Mech Override**: Override gas/temp/distance values
- **Threshold Config**: Adjust emergency thresholds

---

## Migration from Original

### Differences from Original
1. **Unified Protocol**: All messages now have `type`, `source`, `target`, `payload`, `timestamp`
2. **Dot Notation**: Message types use `category.action` format (e.g., `hand.flex_data`)
3. **Modular Server**: Separate engines for emergency, overrides, and message routing
4. **Clean Separation**: Each controller has clearly defined responsibilities

### Testing Steps
1. Start `server_new.js` instead of `server.js`
2. Open `index_new.html` and `admin_new.html`
3. Deploy `main_refactored.py` to controllers (keep as backup name first)
4. Test all features:
   - Flex sensor display
   - Biometric readings
   - Servo control (manual and flex-forwarded)
   - Emergency trigger/clear
   - Admin overrides
5. Once verified, rename `main_refactored.py` to `main.py`

---

## API Reference

### WebSocket Connection
```javascript
const ws = new WebSocket('ws://SERVER_IP:8081');

// Identify client type
ws.send(JSON.stringify({ type: 'identify', client: 'site' }));
```

### Send Servo Command (from site/admin)
```javascript
ws.send(JSON.stringify({
  type: 'site.control_servo',
  source: 'site',
  target: 'server',
  payload: { servo_id: 1, angle: 90 },
  timestamp: Date.now()
}));
```

### Emergency Trigger
```javascript
ws.send(JSON.stringify({
  type: 'site.emergency_trigger',
  source: 'site',
  target: 'server',
  payload: {},
  timestamp: Date.now()
}));
```

---

## Troubleshooting

### Controller won't connect
1. Check WiFi credentials in configuration section
2. Verify server IP address is correct
3. Ensure server is running on port 8081
4. Check for firewall blocking connections

### Servos not responding
1. Check if emergency is active (servos locked)
2. Verify servo pins match hardware
3. Check WebSocket connection status

### Flex forwarding not working
1. Enable "Forward to Servos" in admin panel
2. Verify hand controller is connected
3. Check server console for flex data messages

### Emergency won't clear
1. Press button on hand controller, or
2. Click "Clear Emergency" on home/admin page
3. Both auto and manual emergencies must be cleared
