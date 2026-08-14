const reactor = document.getElementById("reactor");
const statusEl = document.getElementById("status");
const logEl = document.getElementById("log");
const micBtn = document.getElementById("micBtn");

const ws = new WebSocket(`ws://${location.host}/ws`);
const autolisten = new URLSearchParams(location.search).get("autolisten") === "1";

function setStatus(text) {
  statusEl.textContent = text;
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

    const match = data.weather.match(/(-?\d+(?:\.\d+)?)\s*°?F?,\s*(.+?),/);
    if (match) {
      document.getElementById("weatherTemp").textContent = `${match[1]}°F`;
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

function addBubble(role, text) {
  const el = document.createElement("div");
  el.className = `bubble ${role}`;
  el.textContent = text;
  logEl.appendChild(el);
  logEl.scrollTop = logEl.scrollHeight;
}

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
    setStatus("Standing by");
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

ws.addEventListener("open", () => {
  setStatus("Standing by");
  if (autolisten) {
    ws.send(JSON.stringify({ type: "wake" }));
  }
});

ws.addEventListener("message", (event) => {
  const msg = JSON.parse(event.data);
  if (msg.type === "status") {
    setStatus(msg.text);
  } else if (msg.type === "assistant_message") {
    addBubble("assistant", msg.text);
    speak(msg.text, msg.audio_b64);
  }
});

ws.addEventListener("close", () => setStatus("Disconnected"));

// --- Speech recognition (push-to-talk) ---
const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
let recognition = null;
let listening = false;

if (SpeechRecognition) {
  recognition = new SpeechRecognition();
  recognition.continuous = false;
  recognition.interimResults = false;
  recognition.lang = "en-US";

  recognition.onresult = (event) => {
    const transcript = event.results[0][0].transcript.trim();
    if (transcript) {
      addBubble("user", transcript);
      setStatus("Thinking...");
      ws.send(JSON.stringify({ type: "user_message", text: transcript }));
    }
  };

  recognition.onend = () => {
    listening = false;
    reactor.classList.remove("listening");
    micBtn.classList.remove("active");
  };

  recognition.onerror = () => {
    listening = false;
    reactor.classList.remove("listening");
    micBtn.classList.remove("active");
    setStatus("Standing by");
  };

  const startListening = () => {
    if (listening) return;
    listening = true;
    reactor.classList.add("listening");
    micBtn.classList.add("active");
    setStatus("Listening");
    recognition.start();
  };

  const stopListening = () => {
    if (!listening) return;
    recognition.stop();
  };

  micBtn.addEventListener("mousedown", startListening);
  micBtn.addEventListener("touchstart", (e) => {
    e.preventDefault();
    startListening();
  });
  micBtn.addEventListener("mouseup", stopListening);
  micBtn.addEventListener("mouseleave", stopListening);
  micBtn.addEventListener("touchend", stopListening);
} else {
  micBtn.textContent = "Speech recognition not supported — use Chrome";
  micBtn.disabled = true;
}
