// WebSocket Server for Robot Hand & Mech Communication
// Handles communication between hand sensors, mechanical hand, and web interface

const WebSocket = require("ws");
const express = require("express");
const path = require("path");

const app = express();
const PORT = 3001; // Changed from 3000 to avoid conflict
const WS_PORT = 8081; // Changed from 8080 to avoid Apache conflict

// Serve static files from public directory
app.use(express.static(path.join(__dirname, "public")));

// Start HTTP server
app.listen(PORT, () => {
  console.log(`HTTP Server running on http://localhost:${PORT}`);
});

// Create WebSocket server
const wss = new WebSocket.Server({ port: WS_PORT });

// Store connected clients by type
const clients = {
  hand: null,
  mech: null,
  site: [],
  admin: [],
};

// Admin override state
const adminOverride = {
  flex: {
    enabled: false,
    flex_1_2: { min: 45, max: 55 },
    flex_3_4: { min: 45, max: 55 },
    flex_5: { min: 45, max: 55 },
  },
  biometric: {
    enabled: false,
    heart_rate: { min: 70, max: 80 },
    spo2: { min: 97, max: 99 },
  },
  mech: {
    enabled: false,
    gas: { min: 3, max: 7 },
    temperature: { min: 23, max: 25 },
    ultrasonic: { min: 45, max: 55 },
  },
  thresholds: {
    heart_rate: { min: 60, max: 100, critical_low: 40, critical_high: 150 },
    spo2: { min: 95, max: 100, critical_low: 85, critical_high: 100 },
    temperature: { min: 20, max: 48, critical_low: 15, critical_high: 35 },
    gas: { min: 0, max: 30, critical_low: 0, critical_high: 50 },
    ultrasonic: { min: 5, max: 10000, critical_low: 0, critical_high: 2000 },
  },
  autoEmergency: true,
};

// Helper function to get random value in range
function getRandomInRange(min, max, decimals = 1) {
  const value = min + Math.random() * (max - min);
  return decimals === 0 ? Math.round(value) : parseFloat(value.toFixed(decimals));
}

// Store latest data from each client
const latestData = {
  hand: {
    flex: null,
    emergency: false,
    max30102: null,
    timestamp: null,
  },
  mech: {
    servos: null,
    gas: null,
    temperature: null,
    ultrasonic: null,
    timestamp: null,
  },
};

// ============================================================================
// ML PREDICTION SYSTEM
// ============================================================================

const mlData = {
  heart_rate: {
    history: [],
    prediction: null,
    confidence: 0,
    lastUpdate: null,
    trend: 0,
  },
  spo2: {
    history: [],
    prediction: null,
    confidence: 0,
    lastUpdate: null,
    trend: 0,
  },
  temperature: {
    history: [],
    prediction: null,
    confidence: 0,
    lastUpdate: null,
    trend: 0,
  },
  gas: {
    history: [],
    prediction: null,
    confidence: 0,
    lastUpdate: null,
    trend: 0,
  },
  ultrasonic: {
    history: [],
    prediction: null,
    confidence: 0,
    lastUpdate: null,
    trend: 0,
  },
};

const ML_HISTORY_SIZE = 30;
const PREDICTION_THRESHOLD = 3000; // 3 seconds

function addToHistory(sensor, value) {
  if (value === null || value === undefined || isNaN(value)) return;

  const sensorData = mlData[sensor];
  if (!sensorData) return;

  sensorData.history.push({
    value: value,
    timestamp: Date.now(),
  });

  // Keep only last N readings
  if (sensorData.history.length > ML_HISTORY_SIZE) {
    sensorData.history.shift();
  }

  sensorData.lastUpdate = Date.now();
}

function calculateTrend(sensor) {
  const sensorData = mlData[sensor];
  if (!sensorData || sensorData.history.length < 5) return 0;

  // Calculate linear trend using last 10 readings
  const recent = sensorData.history.slice(-10);
  let sumX = 0,
    sumY = 0,
    sumXY = 0,
    sumX2 = 0;

  for (let i = 0; i < recent.length; i++) {
    sumX += i;
    sumY += recent[i].value;
    sumXY += i * recent[i].value;
    sumX2 += i * i;
  }

  const n = recent.length;
  const slope = (n * sumXY - sumX * sumY) / (n * sumX2 - sumX * sumX);

  return slope;
}

function calculateStdDev(values) {
  if (values.length === 0) return 0;
  const mean = values.reduce((sum, v) => sum + v, 0) / values.length;
  const squaredDiffs = values.map((v) => Math.pow(v - mean, 2));
  const variance =
    squaredDiffs.reduce((sum, v) => sum + v, 0) / values.length;
  return Math.sqrt(variance);
}

function calculateConfidence(sensor) {
  const sensorData = mlData[sensor];
  if (!sensorData || sensorData.history.length < 5) return 0;

  // Calculate standard deviation of recent readings
  const recent = sensorData.history.slice(-10).map((h) => h.value);
  const stdDev = calculateStdDev(recent);

  // Map std dev to confidence range (0-5)
  // Lower std dev = higher confidence (lower ±range)
  let confidence;
  if (stdDev < 1) confidence = 1;
  else if (stdDev < 2) confidence = 2;
  else if (stdDev < 3) confidence = 3;
  else if (stdDev < 5) confidence = 4;
  else confidence = 5;

  return confidence;
}

function calculatePrediction(sensor) {
  const sensorData = mlData[sensor];
  if (!sensorData || sensorData.history.length < 3) {
    return null;
  }

  // Use exponential weighted moving average + trend
  const recent = sensorData.history.slice(-10);
  let ewma = recent[0].value;
  const alpha = 0.3; // Smoothing factor

  for (let i = 1; i < recent.length; i++) {
    ewma = alpha * recent[i].value + (1 - alpha) * ewma;
  }

  // Add trend component
  const trend = calculateTrend(sensor);
  const prediction = ewma + trend * 3; // Project 3 time steps ahead

  // Calculate confidence
  const confidence = calculateConfidence(sensor);

  sensorData.prediction = Math.round(prediction * 10) / 10;
  sensorData.confidence = confidence;
  sensorData.trend = trend;

  return {
    value: sensorData.prediction,
    confidence: confidence,
    trend: trend > 0 ? "rising" : trend < 0 ? "falling" : "stable",
  };
}

function getMLPredictions() {
  const predictions = {};
  const now = Date.now();

  Object.keys(mlData).forEach((sensor) => {
    const sensorData = mlData[sensor];

    // Only predict if data is stale
    if (
      sensorData.lastUpdate &&
      now - sensorData.lastUpdate > PREDICTION_THRESHOLD
    ) {
      const pred = calculatePrediction(sensor);
      if (pred) {
        predictions[sensor] = pred;
      }
    }
  });

  return predictions;
}

// ============================================================================
// EMERGENCY DETECTION SYSTEM
// ============================================================================

const vitalRanges = {
  heart_rate: { min: 60, max: 150, critical_low: 40, critical_high: 150 },
  spo2: { min: 85, max: 100, critical_low: 85, critical_high: 100 },
  temperature: { min: 20, max: 48, critical_low: 15, critical_high: 35 },
  gas: { min: 0, max: 30, critical_low: 0, critical_high: 50 },
  ultrasonic: { min: 5, max: 10000, critical_low: 0, critical_high: 2000 },
};

const emergencyState = {
  active: false,
  level: "normal", // 'normal', 'warning', 'critical', 'emergency'
  triggeredBy: [],
  timestamp: null,
  autoTriggered: false,
  lastEmergencyTime: 0,
  cooldownPeriod: 30000, // 30 seconds
};

const previousValues = {
  heart_rate: { value: null, timestamp: null },
  spo2: { value: null, timestamp: null },
};

function checkVitals(sensorData) {
  const abnormal = {
    warning: [],
    critical: [],
    emergency: [],
  };

  // Check each sensor against ranges (use admin thresholds if available)
  Object.keys(vitalRanges).forEach((sensor) => {
    let value = null;

    // Extract sensor value from data
    if (sensor === "heart_rate" && sensorData.hand?.max30102?.heart_rate) {
      value = sensorData.hand.max30102.heart_rate;
    } else if (sensor === "spo2" && sensorData.hand?.max30102?.spo2) {
      value = sensorData.hand.max30102.spo2;
    } else if (sensor === "temperature" && sensorData.mech?.temperature) {
      value = sensorData.mech.temperature;
    } else if (sensor === "gas" && sensorData.mech?.gas?.percent) {
      value = sensorData.mech.gas.percent;
    } else if (sensor === "ultrasonic" && sensorData.mech?.ultrasonic) {
      value = sensorData.mech.ultrasonic;
    }

    if (value === null || value === 0) return;

    // Use admin thresholds if available, otherwise use default vitalRanges
    const ranges = adminOverride.thresholds[sensor] || vitalRanges[sensor];

    // Check for critical violations
    if (value < ranges.critical_low || value > ranges.critical_high) {
      abnormal.emergency.push({ sensor, value, range: "critical" });
    }
    // Check for normal range violations
    else if (value < ranges.min || value > ranges.max) {
      abnormal.warning.push({ sensor, value, range: "normal" });
    }
  });

  // Determine overall status
  let status = "normal";
  if (abnormal.emergency.length > 0) {
    status = "emergency";
  } else if (abnormal.warning.length >= 3) {
    status = "emergency"; // 3+ sensors abnormal = emergency
  } else if (abnormal.warning.length >= 2) {
    status = "critical";
  } else if (abnormal.warning.length >= 1) {
    status = "warning";
  }

  return { status, abnormal: [...abnormal.warning, ...abnormal.emergency] };
}

function detectRapidChanges(sensor, newValue, oldData) {
  if (!oldData || oldData.value === null || newValue === null) return null;

  const timeDiff = (Date.now() - oldData.timestamp) / 1000; // seconds
  if (timeDiff > 10) return null; // Too much time passed

  const valueDiff = Math.abs(newValue - oldData.value);

  // Heart rate: >20 BPM in <5 seconds
  if (sensor === "heart_rate" && valueDiff > 20 && timeDiff < 5) {
    return {
      sensor,
      change: valueDiff,
      time: timeDiff,
      reason: `Heart rate changed ${valueDiff} BPM in ${timeDiff.toFixed(
        1
      )}s`,
    };
  }

  // SpO2: >5% drop in <5 seconds
  if (sensor === "spo2" && valueDiff > 5 && timeDiff < 5) {
    return {
      sensor,
      change: valueDiff,
      time: timeDiff,
      reason: `SpO2 dropped ${valueDiff}% in ${timeDiff.toFixed(1)}s`,
    };
  }

  return null;
}

function triggerEmergency(reason, sensorData, abnormal) {
  const now = Date.now();

  // Cooldown check
  if (now - emergencyState.lastEmergencyTime < emergencyState.cooldownPeriod) {
    return;
  }

  emergencyState.active = true;
  emergencyState.level = "emergency";
  emergencyState.triggeredBy = abnormal;
  emergencyState.timestamp = now;
  emergencyState.autoTriggered = true;
  emergencyState.lastEmergencyTime = now;

  console.log("\n" + "=".repeat(70));
  console.log("🚨 EMERGENCY AUTO-TRIGGERED 🚨");
  console.log("Reason:", reason);
  console.log(
    "Abnormal sensors:",
    abnormal.map((a) => `${a.sensor}: ${a.value}`).join(", ")
  );
  console.log("=".repeat(70) + "\n");

  // Send emergency to hand
  if (clients.hand && clients.hand.readyState === WebSocket.OPEN) {
    clients.hand.send(
      JSON.stringify({
        type: "emergency_auto",
        active: true,
        reason: reason,
        sensors: abnormal.map((a) => a.sensor),
        timestamp: now,
      })
    );
    console.log("[EMERGENCY] Command sent to hand");
  }

  // Send emergency to mech
  if (clients.mech && clients.mech.readyState === WebSocket.OPEN) {
    clients.mech.send(
      JSON.stringify({
        type: "emergency_alert",
        active: true,
      })
    );
  }

  // Broadcast to all website clients
  clients.site.forEach((client) => {
    if (client.readyState === WebSocket.OPEN) {
      client.send(
        JSON.stringify({
          type: "emergency_auto",
          active: true,
          reason: reason,
          abnormal: abnormal,
          timestamp: now,
        })
      );
    }
  });
}

function checkAllSensors() {
  const sensorData = {
    hand: latestData.hand,
    mech: latestData.mech,
  };

  // Check vital signs
  const vitalCheck = checkVitals(sensorData);

  // Check rapid changes
  let rapidChange = null;
  if (latestData.hand?.max30102?.heart_rate) {
    rapidChange = detectRapidChanges(
      "heart_rate",
      latestData.hand.max30102.heart_rate,
      previousValues.heart_rate
    );
    previousValues.heart_rate = {
      value: latestData.hand.max30102.heart_rate,
      timestamp: Date.now(),
    };
  }

  if (!rapidChange && latestData.hand?.max30102?.spo2) {
    rapidChange = detectRapidChanges(
      "spo2",
      latestData.hand.max30102.spo2,
      previousValues.spo2
    );
    previousValues.spo2 = {
      value: latestData.hand.max30102.spo2,
      timestamp: Date.now(),
    };
  }

  // Update emergency state
  const prevLevel = emergencyState.level;
  emergencyState.level = vitalCheck.status;

  // Log status changes
  if (emergencyState.level !== prevLevel) {
    if (emergencyState.level === "normal") {
      console.log("[VITALS] Status: NORMAL ✓");
    } else if (emergencyState.level === "warning") {
      console.log(
        `[VITALS] WARNING: ${vitalCheck.abnormal
          .map((a) => `${a.sensor}: ${a.value}`)
          .join(", ")}`
      );
    } else if (emergencyState.level === "critical") {
      console.log(
        `[VITALS] CRITICAL: Multiple abnormal - ${vitalCheck.abnormal
          .map((a) => `${a.sensor}: ${a.value}`)
          .join(", ")}`
      );
    }
  }

  // Trigger emergency if needed (check if auto-emergency is enabled)
  if (adminOverride.autoEmergency) {
    if (rapidChange) {
      triggerEmergency(rapidChange.reason, sensorData, [
        { sensor: rapidChange.sensor, value: rapidChange.change },
      ]);
    } else if (vitalCheck.status === "emergency" && !emergencyState.active) {
      triggerEmergency(
        "Critical vital signs detected",
        sensorData,
        vitalCheck.abnormal
      );
    }
  } else {
    // Auto-emergency disabled by admin
    if (emergencyState.level === "emergency" && prevLevel !== "emergency") {
      console.log("[VITALS] EMERGENCY conditions detected but auto-trigger disabled by admin");
    }
  }

  // Broadcast vital status to website
  if (clients.site.length > 0 && emergencyState.level !== "normal") {
    clients.site.forEach((client) => {
      if (client.readyState === WebSocket.OPEN) {
        client.send(
          JSON.stringify({
            type: "vital_alert",
            level: emergencyState.level,
            abnormal: vitalCheck.abnormal,
            timestamp: Date.now(),
          })
        );
      }
    });
  }
}

console.log(`WebSocket Server running on ws://localhost:${WS_PORT}`);
console.log("Waiting for connections...\n");

wss.on("connection", (ws) => {
  console.log("New connection established");

  let clientType = null;
  let clientId = Math.random().toString(36).substr(2, 9);

  // Send welcome message
  ws.send(
    JSON.stringify({
      type: "welcome",
      message: "Connected to Robot Control Server",
      clientId: clientId,
    })
  );

  ws.on("message", (message) => {
    try {
      const data = JSON.parse(message);

      // Handle client identification
      if (data.type === "identify") {
        clientType = data.client;

        if (clientType === "hand") {
          if (clients.hand) {
            clients.hand.terminate();
          }
          clients.hand = ws;
          console.log(`✓ Hand client connected (ID: ${clientId})`);
        } else if (clientType === "mech") {
          if (clients.mech) {
            clients.mech.terminate();
          }
          clients.mech = ws;
          console.log(`✓ Mech client connected (ID: ${clientId})`);
        } else if (clientType === "site") {
          clients.site.push(ws);
          console.log(
            `✓ Website client connected (ID: ${clientId}), Total: ${clients.site.length}`
          );

          // Send current data to new website client
          ws.send(
            JSON.stringify({
              type: "initial_data",
              hand: latestData.hand,
              mech: latestData.mech,
            })
          );
        } else if (clientType === "admin") {
          clients.admin.push(ws);
          console.log(
            `✓ Admin client connected (ID: ${clientId}), Total: ${clients.admin.length}`
          );

          // Send current override state to admin
          ws.send(
            JSON.stringify({
              type: "override_state",
              overrideState: adminOverride,
            })
          );
        }

        ws.send(
          JSON.stringify({
            type: "identified",
            client: clientType,
            clientId: clientId,
          })
        );

        return;
      }

      // Handle admin override commands
      if (clientType === "admin") {
        if (data.type === "admin_override") {
          adminOverride.flex = data.overrideState.flex;
          adminOverride.biometric = data.overrideState.biometric;
          adminOverride.mech = data.overrideState.mech;
          
          // Merge thresholds but enforce correct temperature max
          if (data.overrideState.thresholds) {
            adminOverride.thresholds = {
              ...data.overrideState.thresholds,
              temperature: {
                min: 20,
                max: 48,
                critical_low: 15,
                critical_high: 35
              }
            };
          }
          
          adminOverride.autoEmergency = data.overrideState.autoEmergency;
          
          console.log(`[ADMIN] Override state updated`);
          if (adminOverride.flex.enabled) console.log(`  Flex override active`);
          if (adminOverride.biometric.enabled) console.log(`  Biometric override active`);
          if (adminOverride.mech.enabled) console.log(`  Mech override active`);
          
          // Broadcast updated override state to all admins
          clients.admin.forEach((client) => {
            if (client.readyState === WebSocket.OPEN && client !== ws) {
              client.send(
                JSON.stringify({
                  type: "override_state",
                  overrideState: adminOverride,
                })
              );
            }
          });
        }
      }

      // Handle data from hand
      if (clientType === "hand") {
        // REMOVED: control_servos handler - flex sensors should NOT control mech
        // Flex data is for display only (handled by flex_data below)
        
        // Handle flex sensor data from hand
        if (data.type === "flex_data") {
          // Store in latestData
          latestData.hand.flex = data.payload;
          latestData.hand.timestamp = Date.now();
          
          console.log(`[HAND] Flex data: 1-2=${data.payload.flex_1_2.toFixed(1)}%, 3-4=${data.payload.flex_3_4.toFixed(1)}%, 5=${data.payload.flex_5.toFixed(1)}%`);
          
          // Apply admin override if enabled
          let flexDataToSend = data.payload;
          if (adminOverride.flex.enabled) {
            flexDataToSend = {
              flex_1_2: getRandomInRange(
                adminOverride.flex.flex_1_2.min,
                adminOverride.flex.flex_1_2.max
              ),
              flex_3_4: getRandomInRange(
                adminOverride.flex.flex_3_4.min,
                adminOverride.flex.flex_3_4.max
              ),
              flex_5: getRandomInRange(
                adminOverride.flex.flex_5.min,
                adminOverride.flex.flex_5.max
              ),
              timestamp: data.payload.timestamp,
            };
            console.log(`  [OVERRIDE] Flex: 1-2=${flexDataToSend.flex_1_2.toFixed(1)}%, 3-4=${flexDataToSend.flex_3_4.toFixed(1)}%, 5=${flexDataToSend.flex_5.toFixed(1)}%`);
          }
          
          // Forward ONLY to website clients (NOT to mech)
          if (clients.site && clients.site.length > 0) {
            const flexMessage = JSON.stringify({
              type: "flex_data",
              payload: flexDataToSend,
            });
            clients.site.forEach((client) => {
              if (client.readyState === WebSocket.OPEN) {
                client.send(flexMessage);
              }
            });
            console.log(`  → Forwarded to ${clients.site.length} website client(s)`);
          }
        }

        if (data.type === "emergency") {
          const payload = data.payload || data;
          const isActive =
            payload.active !== undefined ? payload.active : data.active;

          latestData.hand.emergency = isActive;
          latestData.hand.timestamp = Date.now();

          console.log(
            `[HAND] 🚨 EMERGENCY ALERT: ${
              isActive ? "ACTIVATED" : "DEACTIVATED"
            }`
          );

          // Alert all clients about emergency
          if (clients.mech && clients.mech.readyState === WebSocket.OPEN) {
            clients.mech.send(
              JSON.stringify({
                type: "emergency_alert",
                active: isActive,
              })
            );
          }

          // Alert website clients immediately
          clients.site.forEach((client) => {
            if (client.readyState === WebSocket.OPEN) {
              client.send(
                JSON.stringify({
                  type: "emergency_alert",
                  active: isActive,
                  timestamp: latestData.hand.timestamp,
                })
              );
            }
          });
        }

        if (data.type === "max30102_data") {
          const payload = data.payload || data;
          latestData.hand.max30102 = {
            heart_rate: payload.heart_rate,
            spo2: payload.spo2,
            status: payload.status,
            red: payload.red,
            ir: payload.ir,
          };
          latestData.hand.timestamp = Date.now();

          // Apply admin override if enabled
          let biometricDataToSend = latestData.hand.max30102;
          if (adminOverride.biometric.enabled) {
            biometricDataToSend = {
              heart_rate: getRandomInRange(
                adminOverride.biometric.heart_rate.min,
                adminOverride.biometric.heart_rate.max,
                0
              ),
              spo2: getRandomInRange(
                adminOverride.biometric.spo2.min,
                adminOverride.biometric.spo2.max,
                0
              ),
              status: "Admin Override",
              red: payload.red,
              ir: payload.ir,
            };
            console.log(`[ADMIN OVERRIDE] HR: ${biometricDataToSend.heart_rate}, SpO2: ${biometricDataToSend.spo2}`);
          }

          // Add to ML history (use actual data, not override)
          addToHistory("heart_rate", payload.heart_rate);
          addToHistory("spo2", payload.spo2);

          console.log(
            `[HAND] ❤️  HR: ${payload.heart_rate} BPM, SpO2: ${payload.spo2}%, Status: ${payload.status}`
          );

          // Broadcast to website clients (with override if enabled)
          clients.site.forEach((client) => {
            if (client.readyState === WebSocket.OPEN) {
              client.send(
                JSON.stringify({
                  type: "max30102_data",
                  data: biometricDataToSend,
                  timestamp: latestData.hand.timestamp,
                })
              );
            }
          });
        }

        // Handle emergency acknowledgment
        if (data.type === "emergency_ack") {
          if (emergencyState.autoTriggered) {
            emergencyState.active = false;
            emergencyState.autoTriggered = false;
            emergencyState.level = "normal";
            console.log(
              `[EMERGENCY] Acknowledged by user at ${new Date().toLocaleTimeString()}`
            );

            // Notify all clients
            clients.site.forEach((client) => {
              if (client.readyState === WebSocket.OPEN) {
                client.send(
                  JSON.stringify({
                    type: "emergency_auto",
                    active: false,
                    timestamp: Date.now(),
                  })
                );
              }
            });
          }
        }
      }

      // Handle data from mech
      if (clientType === "mech") {
        if (data.type === "sensor_data") {
          const payload = data.payload || data;
          latestData.mech.servos = payload.servos;
          latestData.mech.gas = payload.gas;
          latestData.mech.temperature = payload.temperature_c;
          latestData.mech.ultrasonic = payload.distance_cm;
          latestData.mech.timestamp = Date.now();

          // Apply admin override if enabled (only affects ML history with actual data)
          if (payload.temperature_c) addToHistory("temperature", payload.temperature_c);
          if (payload.gas?.percent) addToHistory("gas", payload.gas.percent);
          if (payload.distance_cm) addToHistory("ultrasonic", payload.distance_cm);

          console.log(
            `[MECH] Gas: ${payload.gas ? payload.gas.percent + "%" : "N/A"}, ` +
              `Temp: ${
                payload.temperature_c ? payload.temperature_c + "°C" : "N/A"
              }, ` +
              `Distance: ${
                payload.distance_cm ? payload.distance_cm + "cm" : "N/A"
              }`
          );
          
          if (adminOverride.mech.enabled) {
            console.log(`[ADMIN OVERRIDE] Mech data will be overridden for website clients`);
          }
        }
      }

      // Handle requests from website
      if (clientType === "site" && data.type === "get_data") {
        ws.send(
          JSON.stringify({
            type: "current_data",
            hand: latestData.hand,
            mech: latestData.mech,
          })
        );
      }

      // Handle servo control from website
      if (clientType === "site" && data.type === "control_servo") {
        const overrideMsg = data.emergencyOverride ? " [OVERRIDE]" : "";
        console.log(`[SITE] Servo control: ${data.servo} -> ${data.angle}°${overrideMsg}`);

        // Forward to mech
        if (clients.mech && clients.mech.readyState === WebSocket.OPEN) {
          clients.mech.send(
            JSON.stringify({
              type: "control_servo",
              servo: data.servo,
              angle: data.angle,
              emergencyOverride: data.emergencyOverride || false,
            })
          );
        }
      }

      // Handle bulk servo control from website
      if (clientType === "site" && data.type === "control_servos") {
        const overrideMsg = data.emergencyOverride ? " [OVERRIDE]" : "";
        console.log(
          `[SITE] Bulk servo control: ${JSON.stringify(data.servos)}${overrideMsg}`
        );

        // Forward to mech
        if (clients.mech && clients.mech.readyState === WebSocket.OPEN) {
          clients.mech.send(
            JSON.stringify({
              type: "control_servos",
              source: "website",
              servos: data.servos,
              emergencyOverride: data.emergencyOverride || false,
            })
          );
        }
      }

      // Handle emergency acknowledgment from website
      if (clientType === "site" && data.type === "emergency_ack") {
        if (emergencyState.autoTriggered) {
          emergencyState.active = false;
          emergencyState.autoTriggered = false;
          emergencyState.level = "normal";
          emergencyState.triggeredBy = [];
          console.log(
            `[EMERGENCY] Acknowledged from website at ${new Date().toLocaleTimeString()}`
          );

          // Notify hand to stop blinking
          if (clients.hand && clients.hand.readyState === WebSocket.OPEN) {
            clients.hand.send(
              JSON.stringify({
                type: "emergency_auto",
                active: false,
                timestamp: Date.now(),
              })
            );
          }

          // Notify all website clients
          clients.site.forEach((client) => {
            if (client.readyState === WebSocket.OPEN) {
              client.send(
                JSON.stringify({
                  type: "emergency_auto",
                  active: false,
                  timestamp: Date.now(),
                })
              );
            }
          });
        }
      }
    } catch (error) {
      console.error("Error processing message:", error);
    }
  });

  ws.on("close", () => {
    if (clientType === "hand") {
      console.log(`✗ Hand client disconnected`);
      clients.hand = null;
    } else if (clientType === "mech") {
      console.log(`✗ Mech client disconnected`);
      clients.mech = null;
    } else if (clientType === "site") {
      const index = clients.site.indexOf(ws);
      if (index > -1) {
        clients.site.splice(index, 1);
      }
      console.log(
        `✗ Website client disconnected, Remaining: ${clients.site.length}`
      );
    } else if (clientType === "admin") {
      const index = clients.admin.indexOf(ws);
      if (index > -1) {
        clients.admin.splice(index, 1);
      }
      console.log(
        `✗ Admin client disconnected, Remaining: ${clients.admin.length}`
      );
    }
  });

  ws.on("error", (error) => {
    console.error("WebSocket error:", error);
  });
});

// Broadcast latest data to website clients every 3 seconds
setInterval(() => {
  if (clients.site.length > 0) {
    const predictions = getMLPredictions();

    // Apply admin overrides to data sent to website
    let handData = { ...latestData.hand };
    let mechData = { ...latestData.mech };

    if (adminOverride.flex.enabled && handData.flex) {
      handData.flex = {
        flex_1_2: getRandomInRange(
          adminOverride.flex.flex_1_2.min,
          adminOverride.flex.flex_1_2.max
        ),
        flex_3_4: getRandomInRange(
          adminOverride.flex.flex_3_4.min,
          adminOverride.flex.flex_3_4.max
        ),
        flex_5: getRandomInRange(
          adminOverride.flex.flex_5.min,
          adminOverride.flex.flex_5.max
        ),
        timestamp: handData.flex.timestamp,
      };
    }

    if (adminOverride.biometric.enabled && handData.max30102) {
      handData.max30102 = {
        heart_rate: getRandomInRange(
          adminOverride.biometric.heart_rate.min,
          adminOverride.biometric.heart_rate.max,
          0
        ),
        spo2: getRandomInRange(
          adminOverride.biometric.spo2.min,
          adminOverride.biometric.spo2.max,
          0
        ),
        status: "Admin Override",
        red: handData.max30102.red,
        ir: handData.max30102.ir,
      };
    }

    if (adminOverride.mech.enabled) {
      const gasValue = getRandomInRange(
        adminOverride.mech.gas.min,
        adminOverride.mech.gas.max,
        1
      );
      if (mechData.gas) {
        mechData.gas = {
          percent: gasValue,
          alert: gasValue > adminOverride.thresholds.gas.max,
        };
      }
      mechData.temperature = getRandomInRange(
        adminOverride.mech.temperature.min,
        adminOverride.mech.temperature.max,
        1
      );
      mechData.ultrasonic = getRandomInRange(
        adminOverride.mech.ultrasonic.min,
        adminOverride.mech.ultrasonic.max,
        0
      );
    }

    const message = JSON.stringify({
      type: "periodic_update",
      hand: handData,
      mech: mechData,
      predictions: predictions,
      emergencyState: {
        level: emergencyState.level,
        active: emergencyState.active,
        autoTriggered: emergencyState.autoTriggered,
        triggeredBy: emergencyState.triggeredBy,
      },
      timestamp: Date.now(),
    });

    clients.site.forEach((client) => {
      if (client.readyState === WebSocket.OPEN) {
        client.send(message);
      }
    });

    // Log ML predictions when active
    if (Object.keys(predictions).length > 0) {
      Object.entries(predictions).forEach(([sensor, pred]) => {
        console.log(
          `[ML] ${sensor}: ${pred.value} (±${pred.confidence}) [${pred.trend}]`
        );
      });
    }
  }
}, 3000);

// Monitor vital signs every second
setInterval(() => {
  // Only check if we have recent data
  const now = Date.now();
  const handRecent = latestData.hand.timestamp && (now - latestData.hand.timestamp < 15000);
  const mechRecent = latestData.mech.timestamp && (now - latestData.mech.timestamp < 15000);

  if (handRecent || mechRecent) {
    checkAllSensors();
  }
}, 1000);

// Status endpoint
app.get("/status", (req, res) => {
  res.json({
    server: "Robot Control Server",
    connections: {
      hand: clients.hand ? "connected" : "disconnected",
      mech: clients.mech ? "connected" : "disconnected",
      site: clients.site.length,
    },
    latestData: latestData,
  });
});

console.log("\n" + "=".repeat(70));
console.log("  ROBOT CONTROL SERVER");
console.log("=".repeat(70));
console.log(`  HTTP Server:       http://localhost:${PORT}`);
console.log(`  WebSocket Server:  ws://localhost:${WS_PORT}`);
console.log(`  Status Endpoint:   http://localhost:${PORT}/status`);
console.log("=".repeat(70) + "\n");
