import json, hashlib, re, os, sys, subprocess, base64, time, urllib.request, urllib.error, wave

KEY = "AIzaSyAv43oDbO7axklBYFoI3ZY9l26PXGnF6vM"
PROJECT = "deutsch-karten-90c6a"
FAM = os.environ["FAM"].strip()
SITE = "https://detimoy.netlify.app/"
VOICES = {"de": "de/de_DE/thorsten/high/de_DE-thorsten-high", "ru": "ru/ru_RU/irina/medium/ru_RU-irina-medium"}
VDIR = "voices"
BASE = f"https://firestore.googleapis.com/v1/projects/{PROJECT}/databases/(default)/documents/families/{FAM}"

def http(method, url, body=None, token=None):
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None)
    req.add_header("Content-Type", "application/json")
    if token: req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=60) as r: return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        if e.code == 404: return None
        raise

def fval(v):
    if "stringValue" in v: return v["stringValue"]
    if "arrayValue" in v: return [fval(x) for x in v["arrayValue"].get("values", [])]
    if "mapValue" in v: return {k: fval(x) for k, x in v["mapValue"].get("fields", {}).items()}
    for k in ("integerValue", "doubleValue", "booleanValue"):
        if k in v: return v[k]
    return None

def audio_id(text, lang):
    return lang + "_" + hashlib.sha1((lang + ":" + text).encode()).hexdigest()[:20]

def spoken_ru(t):
    return re.sub(r"\s*/\s*", ", ", re.sub(r"\s*\([^)]*\)", "", t)).strip()

token = http("POST", f"https://identitytoolkit.googleapis.com/v1/accounts:signUp?key={KEY}", {"returnSecureToken": True})["idToken"]

want = {}
for p in ("kids", "dima", "katya"):
    d = http("GET", f"{BASE}/profiles/{p}", token=token)
    if not d: continue
    for w in fval({"mapValue": {"fields": d.get("fields", {})}}).get("custom") or []:
        if isinstance(w, dict) and w.get("de") and w.get("ru"):
            want[audio_id(w["de"].strip(), "de")] = (w["de"].strip(), "de")
            want[audio_id(w["ru"].strip(), "ru")] = (w["ru"].strip(), "ru")

try:
    html = urllib.request.urlopen(SITE, timeout=30).read().decode()
    bundled = json.loads(re.search(r"const AUDIO = (\{.*?\});", html, re.S).group(1))
    for i in [i for i, (t, l) in want.items() if t in bundled.get(l, {})]: del want[i]
except Exception as e:
    print("site manifest not read:", e)

have, page = set(), ""
while True:
    r = http("GET", f"{BASE}/audio?pageSize=300&mask.fieldPaths=lang" + (f"&pageToken={page}" if page else ""), token=token) or {}
    have |= {x["name"].rsplit("/", 1)[1] for x in r.get("documents", [])}
    page = r.get("nextPageToken")
    if not page: break
todo = {i: v for i, v in want.items() if i not in have}
print(f"phrases without built-in audio: {len(want)}; missing in cloud: {len(todo)}")
if not todo:
    print("Nothing to record"); sys.exit(0)

subprocess.run([sys.executable, "-m", "pip", "install", "-q", "piper-tts", "lameenc"], check=True)
os.makedirs(VDIR, exist_ok=True)
langs = {l for _, l in todo.values()}
for lang, path in VOICES.items():
    if lang in langs:
        for ext in ("onnx", "onnx.json"):
            f = f"{VDIR}/{os.path.basename(path)}.{ext}"
            if not os.path.exists(f):
                urllib.request.urlretrieve(f"https://huggingface.co/rhasspy/piper-voices/resolve/main/{path}.{ext}", f)
import lameenc
from piper import PiperVoice
from piper.config import SynthesisConfig
loaded, done, failed = {}, 0, []
for i, (text, lang) in todo.items():
    try:
        if lang not in loaded: loaded[lang] = PiperVoice.load(f"{VDIR}/{os.path.basename(VOICES[lang])}.onnx")
        with wave.open("dk.wav", "wb") as w:
            loaded[lang].synthesize_wav(text if lang == "de" else spoken_ru(text), w, syn_config=SynthesisConfig(length_scale=1.1))
        with wave.open("dk.wav", "rb") as w:
            rate, pcm = w.getframerate(), w.readframes(w.getnframes())
        pcm = b"\0\0" * int(rate * 0.12) + pcm + b"\0\0" * int(rate * 0.15)
        enc = lameenc.Encoder(); enc.set_bit_rate(56); enc.set_in_sample_rate(rate); enc.set_channels(1); enc.set_quality(2)
        mp3 = enc.encode(pcm) + enc.flush()
        http("PATCH", f"{BASE}/audio/{i}", {"fields": {"lang": {"stringValue": lang}, "text": {"stringValue": text},
             "voice": {"stringValue": os.path.basename(VOICES[lang])}, "mp3": {"stringValue": base64.b64encode(mp3).decode()},
             "t": {"integerValue": str(int(time.time() * 1000))}}}, token)
        done += 1; print("recorded:", text)
    except Exception as e:
        failed.append(f"{text}: {e}")
print(f"Recorded {done} of {len(todo)}" + (f"; failed: {failed}" if failed else ""))
if failed: sys.exit(1)
