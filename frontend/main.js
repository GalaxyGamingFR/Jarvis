const reactor = document.getElementById("reactor");
const statusEl = document.getElementById("status");
const logEl = document.getElementById("log");
const micStateEl = document.getElementById("micState");

const ws = new WebSocket(`ws://${location.host}/ws`);
const autolisten = new URLSearchParams(location.search).get("autolisten") === "1";

const IDLE_TIMEOUT_MS = 20000; // how long to keep the hands-free session open after the last thing said
let sessionActive = false;
let idleTimer = null;

function setStatus(text) {
  statusEl.textContent = text;
}

function setMicState(text) {
  micStateEl.textContent = text;
}

function resetIdleTimer() {
  clearTimeout(idleTimer);
  idleTimer = setTimeout(endSession, IDLE_TIMEOUT_MS);
}

function endSession() {
  sessionActive = false;
  clearTimeout(idleTimer);
  setStatus("Standing by");
  setMicState("Off");
}

// Click the reactor anytime to immediately mute — the only manual control, since waking is clap-only.
reactor.addEventListener("click", () => {
  if (recognition && listening) recognition.stop();
  window.speechSynthesis.cancel();
  endSession();
});

function addBubble(role, text) {
  const el = document.createElement("div");
  el.className = `bubble ${role}`;
  el.textContent = text;
  logEl.appendChild(el);
  logEl.scrollTop = logEl.scrollHeight;
}

// --- clock + weather corner panels ---
function tickClock() {
  const now = new Date();
  document.getElementById("clock").textContent = now.toLocaleTimeString([], { hour12: false });
  document.getElementById("clockDate").textContent = now.toLocaleDateString([], {
    weekday: "long",
    month: "short",
    day: "numeric",
  });
}
tickClock();
setInterval(tickClock, 1000);

async function refreshStatus() {
  try {
    const resp = await fetch("/status");
    const data = await resp.json();
    document.getElementById("userName").textContent = data.user_name;
    document.getElementById("location").textContent = data.location;

    const match = data.weather.match(/(-?\d+(?:\.\d+)?)\s*°?C?,\s*(.+?),/);
    if (match) {
      document.getElementById("weatherTemp").textContent = `${match[1]}°C`;
      document.getElementById("weatherDesc").textContent = match[2];
    } else {
      document.getElementById("weatherDesc").textContent = data.weather;
    }
  } catch (e) {
    document.getElementById("weatherDesc").textContent = "Unavailable";
  }
}
refreshStatus();
setInterval(refreshStatus, 5 * 60 * 1000);

function pickBritishVoice() {
  const voices = window.speechSynthesis.getVoices();
  const preferredNames = ["george", "ryan", "james"];
  return (
    voices.find((v) => v.lang === "en-GB" && preferredNames.some((n) => v.name.toLowerCase().includes(n))) ||
    voices.find((v) => v.lang === "en-GB") ||
    voices.find((v) => v.lang.startsWith("en")) ||
    voices[0]
  );
}

function speak(text, audioB64) {
  reactor.classList.add("speaking");
  setStatus("Speaking");

  const onDone = () => {
    reactor.classList.remove("speaking");
    if (sessionActive) {
      resetIdleTimer();
      startListening();
    } else {
      setStatus("Standing by");
    }
  };

  if (audioB64) {
    const audio = new Audio(`data:audio/mpeg;base64,${audioB64}`);
    audio.onended = onDone;
    audio.onerror = onDone;
    audio.play();
    return;
  }

  const utter = new SpeechSynthesisUtterance(text);
  const voice = pickBritishVoice();
  if (voice) utter.voice = voice;
  utter.rate = 1.0;
  utter.onend = onDone;
  utter.onerror = onDone;
  window.speechSynthesis.speak(utter);
}

function beginSession() {
  sessionActive = true;
  setMicState("Active");
  resetIdleTimer();
}

ws.addEventListener("open", () => {
  setStatus("Standing by");
  if (autolisten) {
    beginSession();
    ws.send(JSON.stringify({ type: "wake" }));
  }
});

ws.addEventListener("message", (event) => {
  const msg = JSON.parse(event.data);
  if (msg.type === "wake_push") {
    // clap_trigger.py found this tab already open and refocused it instead of opening a new one
    beginSession();
  } else if (msg.type === "status") {
    setStatus(msg.text);
  } else if (msg.type === "assistant_message") {
    addBubble("assistant", msg.text);
    speak(msg.text, msg.audio_b64);
  }
});

ws.addEventListener("close", () => setStatus("Disconnected"));

// --- Speech recognition, driven entirely by clap-wake (no manual control) ---
const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
let recognition = null;
let listening = false;
let gotResult = false;

function startListening() {
  if (!recognition || listening) return;
  listening = true;
  gotResult = false;
  reactor.classList.add("listening");
  setMicState("Listening");
  setStatus("Listening");
  recognition.start();
}

if (SpeechRecognition) {
  recognition = new SpeechRecognition();
  recognition.continuous = false;
  recognition.interimResults = false;
  recognition.lang = "en-US";

  recognition.onresult = (event) => {
    gotResult = true;
    const transcript = event.results[0][0].transcript.trim();
    if (transcript) {
      addBubble("user", transcript);
      setStatus("Thinking...");
      resetIdleTimer();
      ws.send(JSON.stringify({ type: "user_message", text: transcript }));
    }
  };

  recognition.onend = () => {
    listening = false;
    reactor.classList.remove("listening");
    if (!gotResult && sessionActive) {
      endSession();
    }
  };

  recognition.onerror = () => {
    listening = false;
    reactor.classList.remove("listening");
    if (sessionActive) endSession();
  };
} else {
  setStatus("Speech recognition not supported — use Chrome");
}
