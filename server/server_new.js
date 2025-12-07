/**
 * ============================================================================
 * ROBOT CONTROL SERVER - Refactored Version
 * ============================================================================
 * 
 * Role: Central WebSocket server and state manager for the robotics system.
 * 
 * Message Types Handled:
 * - From Hand: hand.flex_data, hand.biometric_data, hand.emergency_manual
 * - From Mech: mech.sensor_data
 * - From Site: site.control_servo, site.control_servos, site.emergency_ack
 * - From Admin: admin.override_state
 * - Broadcast: emergency.status, server.periodic_update
 * 
 * Configuration:
 * - HTTP_PORT: 3001 (Express static server)
 * - WS_PORT: 8081 (WebSocket server)
 * 
 * Migration Notes:
 * - New message schema uses dot notation (e.g., "hand.flex_data" vs "flex_data")
 * - All messages include source, target, payload, timestamp fields
 * - Flex override now has per-sensor enable and forwardToServos option
 * - To switch: rename this file to server.js after testing
 * ============================================================================
 */

const WebSocket = require("ws");
const express = require("express");
const path = require("path");

// ============================================================================
// CONFIGURATION
// ============================================================================

const HTTP_PORT = 3001;
const WS_PORT = 8081;

// ============================================================================
// EXPRESS SERVER SETUP
// ============================================================================

const app = express();
app.use(express.static(path.join(__dirname, "public")));

app.listen(HTTP_PORT, () => {
  console.log(`[HTTP] Server running on http://localhost:${HTTP_PORT}`);
});

// ============================================================================
// WEBSOCKET SERVER SETUP
// ============================================================================

const wss = new WebSocket.Server({ port: WS_PORT });

console.log(`[WS] Server running on ws://localhost:${WS_PORT}`);

// ============================================================================
// CLIENT REGISTRY
// ============================================================================

const clients = {
  hand: null,
  mech: null,
  site: [],
  admin: []
};

// ============================================================================
// STATE MANAGEMENT
// ============================================================================

const latestData = {
  hand: {
    flex: null,
    biometric: null,
    emergency: false,
    timestamp: null
  },
  mech: {
    servos: null,
    gas: null,
    temperature: null,
    ultrasonic: null,
    timestamp: null
  }
};

const emergencyState = {
  active: false,
  level: "normal", // normal, warning, critical, emergency
  autoTriggered: false,
  manualTriggered: false,
  triggeredBy: [],
  timestamp: null,
  cooldownUntil: 0
};

// Track vital level separately from emergency state
let previousVitalLevel = "normal";

const adminOverride = {
  flex: {
    enabled: false,
    forwardToServos: false,
    flex_1: { enabled: false, min: 45, max: 55 },
    flex_2: { enabled: false, min: 45, max: 55 },
    flex_3: { enabled: false, min: 45, max: 55 },
    flex_4: { enabled: false, min: 45, max: 55 },
    flex_5: { enabled: false, min: 45, max: 55 }
  },
  biometric: {
    enabled: false,
    heart_rate: { min: 70, max: 80 },
    spo2: { min: 97, max: 99 }
  },
  mech: {
    enabled: false,
    gas: { min: 3, max: 7 },
    temperature: { min: 23, max: 25 },
    ultrasonic: { min: 45, max: 55 }
  },
  thresholds: {
    heart_rate: { min: 60, max: 100, critical_low: 40, critical_high: 150 },
    spo2: { min: 95, max: 100, critical_low: 85, critical_high: 100 },
    temperature: { min: 20, max: 48, critical_low: 15, critical_high: 35 },
    gas: { min: 0, max: 30, critical_low: 0, critical_high: 50 },
    ultrasonic: { min: 5, max: 10000, critical_low: 0, critical_high: 2000 }
  },
  autoEmergency: true
};

// ============================================================================
// UTILITY FUNCTIONS
// ============================================================================

function getRandomInRange(min, max, decimals = 1) {
  const value = min + Math.random() * (max - min);
  return decimals === 0 ? Math.round(value) : parseFloat(value.toFixed(decimals));
}

function createMessage(type, source, target, payload) {
  return {
    type,
    source,
    target,
    payload,
    timestamp: Date.now()
  };
}

function sendToClient(client, message) {
  if (client && client.readyState === WebSocket.OPEN) {
    client.send(JSON.stringify(message));
    return true;
  }
  return false;
}

function broadcastToSites(message) {
  clients.site.forEach(client => sendToClient(client, message));
}

function broadcastToAdmins(message) {
  clients.admin.forEach(client => sendToClient(client, message));
}

function broadcastToAll(message) {
  sendToClient(clients.hand, message);
  sendToClient(clients.mech, message);
  broadcastToSites(message);
  broadcastToAdmins(message);
}

// ============================================================================
// FLEX TO SERVO CONVERSION
// ============================================================================

function flexPercentToServoAngle(percent) {
  // Convert flex percentage (0-100) to servo angle (0-180)
  return Math.round((percent / 100) * 180);
}

function getServoAnglesFromFlex(flexData) {
  const servos = {};
  
  if (flexData.flex_1 !== undefined) {
    servos.servo_1 = flexPercentToServoAngle(flexData.flex_1);
  }
  
  if (flexData.flex_2 !== undefined) {
    servos.servo_2 = flexPercentToServoAngle(flexData.flex_2);
  }
  
  if (flexData.flex_3 !== undefined) {
    servos.servo_3 = flexPercentToServoAngle(flexData.flex_3);
  }
  
  if (flexData.flex_4 !== undefined) {
    servos.servo_4 = flexPercentToServoAngle(flexData.flex_4);
  }
  
  if (flexData.flex_5 !== undefined) {
    servos.servo_5 = flexPercentToServoAngle(flexData.flex_5);
  }
  
  return servos;
}

// ============================================================================
// OVERRIDE ENGINE
// ============================================================================

function applyFlexOverride(realFlexData) {
  if (!adminOverride.flex.enabled) {
    return realFlexData;
  }
  
  const overridden = { ...realFlexData };
  
  if (adminOverride.flex.flex_1.enabled) {
    overridden.flex_1 = getRandomInRange(
      adminOverride.flex.flex_1.min,
      adminOverride.flex.flex_1.max
    );
  }
  
  if (adminOverride.flex.flex_2.enabled) {
    overridden.flex_2 = getRandomInRange(
      adminOverride.flex.flex_2.min,
      adminOverride.flex.flex_2.max
    );
  }
  
  if (adminOverride.flex.flex_3.enabled) {
    overridden.flex_3 = getRandomInRange(
      adminOverride.flex.flex_3.min,
      adminOverride.flex.flex_3.max
    );
  }
  
  if (adminOverride.flex.flex_4.enabled) {
    overridden.flex_4 = getRandomInRange(
      adminOverride.flex.flex_4.min,
      adminOverride.flex.flex_4.max
    );
  }
  
  if (adminOverride.flex.flex_5.enabled) {
    overridden.flex_5 = getRandomInRange(
      adminOverride.flex.flex_5.min,
      adminOverride.flex.flex_5.max
    );
  }
  
  return overridden;
}

function applyBiometricOverride(realBiometric) {
  if (!adminOverride.biometric.enabled || !realBiometric) {
    return realBiometric;
  }
  
  return {
    ...realBiometric,
    heart_rate: getRandomInRange(
      adminOverride.biometric.heart_rate.min,
      adminOverride.biometric.heart_rate.max,
      0
    ),
    spo2: getRandomInRange(
      adminOverride.biometric.spo2.min,
      adminOverride.biometric.spo2.max,
      0
    )
  };
}

function applyMechOverride(realMech) {
  if (!adminOverride.mech.enabled) {
    return realMech;
  }
  
  const overridden = { ...realMech };
  
  if (realMech.gas) {
    const gasValue = getRandomInRange(adminOverride.mech.gas.min, adminOverride.mech.gas.max, 1);
    overridden.gas = {
      percent: gasValue,
      alert: gasValue > adminOverride.thresholds.gas.max
    };
  }
  
  overridden.temperature = getRandomInRange(
    adminOverride.mech.temperature.min,
    adminOverride.mech.temperature.max,
    1
  );
  
  overridden.ultrasonic = getRandomInRange(
    adminOverride.mech.ultrasonic.min,
    adminOverride.mech.ultrasonic.max,
    0
  );
  
  return overridden;
}

// ============================================================================
// EMERGENCY ENGINE
// ============================================================================

function checkVitals() {
  const abnormal = [];
  const thresholds = adminOverride.thresholds;
  
  // Check mech sensors only (not biometric per original design)
  if (latestData.mech.temperature !== null) {
    const temp = latestData.mech.temperature;
    if (temp < thresholds.temperature.critical_low || temp > thresholds.temperature.critical_high) {
      abnormal.push({ sensor: "temperature", value: temp, level: "critical" });
    } else if (temp < thresholds.temperature.min || temp > thresholds.temperature.max) {
      abnormal.push({ sensor: "temperature", value: temp, level: "warning" });
    }
  }
  
  if (latestData.mech.gas?.percent !== undefined) {
    const gas = latestData.mech.gas.percent;
    if (gas > thresholds.gas.critical_high) {
      abnormal.push({ sensor: "gas", value: gas, level: "critical" });
    } else if (gas > thresholds.gas.max) {
      abnormal.push({ sensor: "gas", value: gas, level: "warning" });
    }
  }
  
  if (latestData.mech.ultrasonic !== null) {
    const dist = latestData.mech.ultrasonic;
    if (dist < thresholds.ultrasonic.critical_low) {
      abnormal.push({ sensor: "ultrasonic", value: dist, level: "critical" });
    } else if (dist < thresholds.ultrasonic.min) {
      abnormal.push({ sensor: "ultrasonic", value: dist, level: "warning" });
    }
  }
  
  // Determine overall level
  const criticalCount = abnormal.filter(a => a.level === "critical").length;
  const warningCount = abnormal.filter(a => a.level === "warning").length;
  
  let level = "normal";
  if (criticalCount > 0) {
    level = "emergency";
  } else if (warningCount >= 3) {
    level = "emergency";
  } else if (warningCount >= 2) {
    level = "critical";
  } else if (warningCount >= 1) {
    level = "warning";
  }
  
  return { level, abnormal };
}

function triggerAutoEmergency(reason, abnormal) {
  const now = Date.now();
  
  // Cooldown check (30 seconds)
  if (now < emergencyState.cooldownUntil) {
    return;
  }
  
  emergencyState.active = true;
  emergencyState.level = "emergency";
  emergencyState.autoTriggered = true;
  emergencyState.triggeredBy = abnormal;
  emergencyState.timestamp = now;
  emergencyState.cooldownUntil = now + 30000;
  
  console.log("\n" + "=".repeat(60));
  console.log("🚨 AUTO-EMERGENCY TRIGGERED");
  console.log(`Reason: ${reason}`);
  console.log(`Sensors: ${abnormal.map(a => `${a.sensor}: ${a.value}`).join(", ")}`);
  console.log("=".repeat(60) + "\n");
  
  // Notify mech to lock servos
  sendToClient(clients.mech, createMessage(
    "emergency.alert",
    "server",
    "mech",
    { active: true }
  ));
  
  // Broadcast to all
  broadcastEmergencyStatus();
}

function clearEmergency() {
  emergencyState.active = false;
  emergencyState.level = "normal";
  emergencyState.autoTriggered = false;
  emergencyState.manualTriggered = false;
  emergencyState.triggeredBy = [];
  emergencyState.timestamp = Date.now();
  
  console.log("[EMERGENCY] Cleared");
  
  // Notify mech to unlock
  sendToClient(clients.mech, createMessage(
    "emergency.alert",
    "server",
    "mech",
    { active: false }
  ));
  
  broadcastEmergencyStatus();
}

function broadcastEmergencyStatus() {
  const message = createMessage(
    "emergency.status",
    "server",
    "broadcast",
    {
      active: emergencyState.active,
      level: emergencyState.level,
      autoTriggered: emergencyState.autoTriggered,
      manualTriggered: emergencyState.manualTriggered,
      triggeredBy: emergencyState.triggeredBy
    }
  );
  
  broadcastToAll(message);
}

// ============================================================================
// MESSAGE HANDLERS
// ============================================================================

function handleHandMessage(ws, data) {
  const msgType = data.type;
  const payload = data.payload || data;
  
  switch (msgType) {
    case "hand.flex_data":
    case "flex_data": {
      // Store raw flex data
      latestData.hand.flex = payload;
      latestData.hand.timestamp = Date.now();
      
      console.log(`[HAND] Flex: 1-2=${payload.flex_1_2?.toFixed(1)}%, 3-4=${payload.flex_3_4?.toFixed(1)}%, 5=${payload.flex_5?.toFixed(1)}%`);
      
      // Apply override for display
      const displayFlex = applyFlexOverride(payload);
      
      // Send to websites (as if from hand)
      const flexMsg = createMessage("hand.flex_data", "hand", "site", displayFlex);
      broadcastToSites(flexMsg);
      
      // Forward to servos if enabled
      if (adminOverride.flex.forwardToServos) {
        const servoAngles = getServoAnglesFromFlex(displayFlex);
        const servoMsg = createMessage("server.control_servos", "server", "mech", {
          servos: servoAngles,
          source: "flex_forward"
        });
        sendToClient(clients.mech, servoMsg);
        console.log(`  → Servo forward: ${JSON.stringify(servoAngles)}`);
      }
      break;
    }
    
    case "hand.biometric_data":
    case "max30102_data": {
      latestData.hand.biometric = payload;
      latestData.hand.timestamp = Date.now();
      
      console.log(`[HAND] ❤️ HR: ${payload.heart_rate} BPM, SpO2: ${payload.spo2}%`);
      
      // Apply override for display
      const displayBio = applyBiometricOverride(payload);
      
      const bioMsg = createMessage("hand.biometric_data", "hand", "site", displayBio);
      broadcastToSites(bioMsg);
      break;
    }
    
    case "hand.emergency_manual":
    case "emergency": {
      const isActive = payload.active;
      
      emergencyState.active = isActive;
      emergencyState.manualTriggered = isActive;
      emergencyState.timestamp = Date.now();
      
      if (!isActive) {
        clearEmergency();
      } else {
        console.log(`[HAND] 🚨 Manual emergency: ${isActive ? "ACTIVATED" : "CLEARED"}`);
        broadcastEmergencyStatus();
        
        // Notify mech
        sendToClient(clients.mech, createMessage(
          "emergency.alert",
          "server",
          "mech",
          { active: true }
        ));
      }
      break;
    }
    
    case "emergency_ack": {
      if (emergencyState.active) {
        clearEmergency();
        console.log("[HAND] Emergency acknowledged");
      }
      break;
    }
  }
}

function handleMechMessage(ws, data) {
  const msgType = data.type;
  const payload = data.payload || data;
  
  switch (msgType) {
    case "mech.sensor_data":
    case "sensor_data": {
      latestData.mech.servos = payload.servos;
      latestData.mech.gas = payload.gas;
      latestData.mech.temperature = payload.temperature_c;
      latestData.mech.ultrasonic = payload.distance_cm;
      latestData.mech.timestamp = Date.now();
      
      console.log(`[MECH] Gas: ${payload.gas?.percent?.toFixed(1) || "N/A"}%, Temp: ${payload.temperature_c?.toFixed(1) || "N/A"}°C, Dist: ${payload.distance_cm?.toFixed(1) || "N/A"}cm`);
      break;
    }
  }
}

function handleSiteMessage(ws, data) {
  const msgType = data.type;
  const payload = data.payload || data;
  
  switch (msgType) {
    case "get_data": {
      ws.send(JSON.stringify(createMessage(
        "server.current_data",
        "server",
        "site",
        {
          hand: latestData.hand,
          mech: latestData.mech
        }
      )));
      break;
    }
    
    case "site.control_servo":
    case "control_servo": {
      const override = data.emergencyOverride || false;
      console.log(`[SITE] Servo: ${data.servo} → ${data.angle}°${override ? " [OVERRIDE]" : ""}`);
      
      sendToClient(clients.mech, createMessage(
        "server.control_servo",
        "server",
        "mech",
        {
          servo: data.servo,
          angle: data.angle,
          emergencyOverride: override
        }
      ));
      break;
    }
    
    case "site.control_servos":
    case "control_servos": {
      const override = data.emergencyOverride || false;
      console.log(`[SITE] Bulk servo: ${JSON.stringify(data.servos)}${override ? " [OVERRIDE]" : ""}`);
      
      sendToClient(clients.mech, createMessage(
        "server.control_servos",
        "server",
        "mech",
        {
          servos: data.servos,
          emergencyOverride: override
        }
      ));
      break;
    }
    
    case "emergency_alert": {
      const isActive = data.active;
      emergencyState.active = isActive;
      emergencyState.manualTriggered = isActive;
      emergencyState.timestamp = Date.now();
      
      console.log(`[SITE] 🚨 Emergency: ${isActive ? "TRIGGERED" : "CLEARED"}`);
      
      if (isActive) {
        sendToClient(clients.mech, createMessage(
          "emergency.alert",
          "server",
          "mech",
          { active: true }
        ));
      }
      
      broadcastEmergencyStatus();
      break;
    }
    
    case "emergency_ack": {
      if (emergencyState.active) {
        clearEmergency();
        console.log("[SITE] Emergency acknowledged");
      }
      break;
    }
  }
}

function handleAdminMessage(ws, data) {
  const msgType = data.type;
  
  switch (msgType) {
    case "admin.override_state":
    case "admin_override": {
      const newState = data.overrideState;
      
      // Update override state
      if (newState.flex) adminOverride.flex = newState.flex;
      if (newState.biometric) adminOverride.biometric = newState.biometric;
      if (newState.mech) adminOverride.mech = newState.mech;
      if (newState.thresholds) {
        // Deep merge thresholds to preserve all fields
        adminOverride.thresholds = {
          heart_rate: {
            ...adminOverride.thresholds.heart_rate,
            ...(newState.thresholds.heart_rate || {})
          },
          spo2: {
            ...adminOverride.thresholds.spo2,
            ...(newState.thresholds.spo2 || {})
          },
          temperature: {
            ...adminOverride.thresholds.temperature,
            ...(newState.thresholds.temperature || {}),
            max: 48  // Enforce temperature max
          },
          gas: {
            ...adminOverride.thresholds.gas,
            ...(newState.thresholds.gas || {})
          },
          ultrasonic: {
            ...adminOverride.thresholds.ultrasonic,
            ...(newState.thresholds.ultrasonic || {})
          }
        };
      }
      if (newState.autoEmergency !== undefined) {
        adminOverride.autoEmergency = newState.autoEmergency;
      }
      
      console.log(`[ADMIN] Override updated - Flex: ${adminOverride.flex.enabled}, Bio: ${adminOverride.biometric.enabled}, Mech: ${adminOverride.mech.enabled}, ForwardServos: ${adminOverride.flex.forwardToServos}`);
      
      // Broadcast to other admins
      clients.admin.forEach(client => {
        if (client !== ws && client.readyState === WebSocket.OPEN) {
          client.send(JSON.stringify(createMessage(
            "admin.override_state",
            "server",
            "admin",
            { overrideState: adminOverride }
          )));
        }
      });
      break;
    }
    
    case "emergency_alert": {
      handleSiteMessage(ws, data); // Same handling as site
      break;
    }
    
    case "emergency_ack": {
      handleSiteMessage(ws, data);
      break;
    }
  }
}

// ============================================================================
// WEBSOCKET CONNECTION HANDLER
// ============================================================================

wss.on("connection", (ws) => {
  console.log("[WS] New connection");
  
  let clientType = null;
  const clientId = Math.random().toString(36).substr(2, 9);
  
  // Send welcome
  ws.send(JSON.stringify(createMessage(
    "server.welcome",
    "server",
    "client",
    { message: "Connected to Robot Control Server", clientId }
  )));
  
  ws.on("message", (message) => {
    try {
      const data = JSON.parse(message);
      
      // Handle identification
      if (data.type === "identify") {
        clientType = data.client;
        
        switch (clientType) {
          case "hand":
            if (clients.hand) clients.hand.terminate();
            clients.hand = ws;
            console.log(`✓ Hand connected (${clientId})`);
            break;
            
          case "mech":
            if (clients.mech) clients.mech.terminate();
            clients.mech = ws;
            console.log(`✓ Mech connected (${clientId})`);
            break;
            
          case "site":
            clients.site.push(ws);
            console.log(`✓ Site connected (${clientId}), total: ${clients.site.length}`);
            
            // Send initial data
            ws.send(JSON.stringify(createMessage(
              "server.initial_data",
              "server",
              "site",
              {
                hand: latestData.hand,
                mech: latestData.mech,
                emergencyState: {
                  active: emergencyState.active,
                  level: emergencyState.level,
                  autoTriggered: emergencyState.autoTriggered,
                  manualTriggered: emergencyState.manualTriggered
                }
              }
            )));
            break;
            
          case "admin":
            clients.admin.push(ws);
            console.log(`✓ Admin connected (${clientId}), total: ${clients.admin.length}`);
            
            // Send current override state
            ws.send(JSON.stringify(createMessage(
              "admin.override_state",
              "server",
              "admin",
              { overrideState: adminOverride }
            )));
            break;
        }
        
        ws.send(JSON.stringify(createMessage(
          "server.identified",
          "server",
          clientType,
          { client: clientType, clientId }
        )));
        return;
      }
      
      // Route messages based on client type
      switch (clientType) {
        case "hand":
          handleHandMessage(ws, data);
          break;
        case "mech":
          handleMechMessage(ws, data);
          break;
        case "site":
          handleSiteMessage(ws, data);
          break;
        case "admin":
          handleAdminMessage(ws, data);
          break;
      }
      
    } catch (error) {
      console.error("[WS] Message error:", error.message);
    }
  });
  
  ws.on("close", () => {
    switch (clientType) {
      case "hand":
        console.log("✗ Hand disconnected");
        clients.hand = null;
        break;
      case "mech":
        console.log("✗ Mech disconnected");
        clients.mech = null;
        break;
      case "site":
        const siteIdx = clients.site.indexOf(ws);
        if (siteIdx > -1) clients.site.splice(siteIdx, 1);
        console.log(`✗ Site disconnected, remaining: ${clients.site.length}`);
        break;
      case "admin":
        const adminIdx = clients.admin.indexOf(ws);
        if (adminIdx > -1) clients.admin.splice(adminIdx, 1);
        console.log(`✗ Admin disconnected, remaining: ${clients.admin.length}`);
        break;
    }
  });
  
  ws.on("error", (error) => {
    console.error("[WS] Error:", error.message);
  });
});

// ============================================================================
// PERIODIC TASKS
// ============================================================================

// Broadcast emergency status every second
setInterval(() => {
  broadcastEmergencyStatus();
}, 1000);

// Periodic update to sites every 3 seconds
setInterval(() => {
  if (clients.site.length === 0) return;
  
  // Apply overrides
  const handData = {
    ...latestData.hand,
    flex: latestData.hand.flex ? applyFlexOverride(latestData.hand.flex) : null,
    biometric: applyBiometricOverride(latestData.hand.biometric)
  };
  
  const mechData = applyMechOverride(latestData.mech);
  
  const message = createMessage(
    "server.periodic_update",
    "server",
    "site",
    {
      hand: handData,
      mech: mechData,
      emergencyState: {
        active: emergencyState.active,
        level: emergencyState.level,
        autoTriggered: emergencyState.autoTriggered,
        manualTriggered: emergencyState.manualTriggered,
        triggeredBy: emergencyState.triggeredBy
      }
    }
  );
  
  broadcastToSites(message);
}, 3000);

// Monitor vitals every second
setInterval(() => {
  const now = Date.now();
  const mechRecent = latestData.mech.timestamp && (now - latestData.mech.timestamp < 15000);
  
  if (!mechRecent) return;
  
  const vitalCheck = checkVitals();
  
  // Update emergency level only if not in active emergency
  if (!emergencyState.active) {
    emergencyState.level = vitalCheck.level;
  }
  
  // Trigger auto-emergency if enabled
  if (adminOverride.autoEmergency && vitalCheck.level === "emergency" && !emergencyState.active) {
    triggerAutoEmergency("Critical sensor readings detected", vitalCheck.abnormal);
  }
  
  // Broadcast vital alert if vital level changed (independent of emergency state)
  if (vitalCheck.level !== previousVitalLevel) {
    previousVitalLevel = vitalCheck.level;
    const alertMsg = createMessage(
      "server.vital_alert",
      "server",
      "site",
      {
        level: vitalCheck.level,
        abnormal: vitalCheck.abnormal
      }
    );
    broadcastToSites(alertMsg);
  }
}, 1000);

// Forward flex override to servos (interval-based when hand not sending)
setInterval(() => {
  if (!adminOverride.flex.enabled || !adminOverride.flex.forwardToServos) {
    return;
  }
  
  // Check if hand is sending (recent data)
  const now = Date.now();
  const handActive = latestData.hand.timestamp && (now - latestData.hand.timestamp < 500);
  
  // If hand is active, the flex_data handler already forwards
  if (handActive) return;
  
  // Generate override values and forward
  const overrideFlex = {
    flex_1_2: adminOverride.flex.flex_1_2.enabled 
      ? getRandomInRange(adminOverride.flex.flex_1_2.min, adminOverride.flex.flex_1_2.max)
      : 50,
    flex_3_4: adminOverride.flex.flex_3_4.enabled
      ? getRandomInRange(adminOverride.flex.flex_3_4.min, adminOverride.flex.flex_3_4.max)
      : 50,
    flex_5: adminOverride.flex.flex_5.enabled
      ? getRandomInRange(adminOverride.flex.flex_5.min, adminOverride.flex.flex_5.max)
      : 50
  };
  
  const servoAngles = getServoAnglesFromFlex(overrideFlex);
  
  sendToClient(clients.mech, createMessage(
    "server.control_servos",
    "server",
    "mech",
    {
      servos: servoAngles,
      source: "override_forward"
    }
  ));
  
  // Also send flex data to sites
  broadcastToSites(createMessage("hand.flex_data", "hand", "site", overrideFlex));
  
}, 100);

// ============================================================================
// STATUS ENDPOINT
// ============================================================================

app.get("/status", (req, res) => {
  res.json({
    server: "Robot Control Server (Refactored)",
    version: "2.0.0",
    connections: {
      hand: clients.hand ? "connected" : "disconnected",
      mech: clients.mech ? "connected" : "disconnected",
      site: clients.site.length,
      admin: clients.admin.length
    },
    latestData,
    emergencyState: {
      active: emergencyState.active,
      level: emergencyState.level,
      autoTriggered: emergencyState.autoTriggered,
      manualTriggered: emergencyState.manualTriggered
    },
    overrides: {
      flex: adminOverride.flex.enabled,
      biometric: adminOverride.biometric.enabled,
      mech: adminOverride.mech.enabled,
      forwardToServos: adminOverride.flex.forwardToServos
    }
  });
});

// ============================================================================
// STARTUP BANNER
// ============================================================================

console.log("\n" + "=".repeat(60));
console.log("  ROBOT CONTROL SERVER (Refactored)");
console.log("=".repeat(60));
console.log(`  HTTP:   http://localhost:${HTTP_PORT}`);
console.log(`  WS:     ws://localhost:${WS_PORT}`);
console.log(`  Status: http://localhost:${HTTP_PORT}/status`);
console.log("=".repeat(60) + "\n");
