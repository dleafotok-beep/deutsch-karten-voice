import json, hashlib, re, os, sys, time, html, base64, urllib.request, urllib.error

KEY = "AIzaSyAv43oDbO7axklBYFoI3ZY9l26PXGnF6vM"
PROJECT = "deutsch-karten-90c6a"
FAM = os.environ["FAM"].strip()
AZ_KEY = os.environ["AZURE_KEY"].strip()
AZ_REGION = os.environ.get("AZURE_REGION", "germanywestcentral").strip() or "germanywestcentral"
SITE = "https://detimoy.netlify.app/"
VERSION = "azure-v1"
VOICE = {"de": "de-DE-ConradNeural", "de2": "de-DE-SeraphinaMultilingualNeural", "ru": "ru-RU-SvetlanaNeural"}
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

def tts(text, voice):
    ssml = f"<speak version='1.0' xml:lang='{voice[:5]}'><voice name='{voice}'>{html.escape(text)}</voice></speak>"
    for attempt in range(10):
        req = urllib.request.Request(f"https://{AZ_REGION}.tts.speech.microsoft.com/cognitiveservices/v1", data=ssml.encode(), method="POST",
              headers={"Ocp-Apim-Subscription-Key": AZ_KEY, "Content-Type": "application/ssml+xml",
                       "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3", "User-Agent": "deutsch-karten"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read()
                if len(data) > 1000: return base64.b64encode(data).decode()
        except urllib.error.HTTPError as e:
            if e.code in (401, 403): sys.exit("Microsoft rejected the key (AZURE_KEY) or region (AZURE_REGION)")
            if e.code not in (429, 500, 502, 503, 504): raise
            time.sleep(float(e.headers.get("Retry-After") or 0) or 3 * (attempt + 1)); continue
        except Exception:
            time.sleep(3 * (attempt + 1)); continue
    raise RuntimeError("Microsoft did not answer")

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
    page_html = urllib.request.urlopen(SITE, timeout=30).read().decode()
    bundled = json.loads(re.search(r"const AUDIO = (\{.*?\});", page_html, re.S).group(1))
    if "conrad" in page_html.lower():  # only trust the site's own files once it carries the Microsoft voices
        for i in [i for i, (t, l) in want.items() if t in bundled.get(l, {})]: del want[i]
except Exception as e:
    print("site manifest not read:", e)

have, page = set(), ""
while True:
    r = http("GET", f"{BASE}/audio?pageSize=300&mask.fieldPaths=voice" + (f"&pageToken={page}" if page else ""), token=token) or {}
    have |= {x["name"].rsplit("/", 1)[1] for x in r.get("documents", []) if x.get("fields", {}).get("voice", {}).get("stringValue") == VERSION}
    page = r.get("nextPageToken")
    if not page: break
todo = {i: v for i, v in want.items() if i not in have}
print(f"phrases without built-in audio: {len(want)}; to record: {len(todo)}")
done, failed = 0, []
for i, (text, lang) in todo.items():
    try:
        fields = {"lang": {"stringValue": lang}, "text": {"stringValue": text}, "voice": {"stringValue": VERSION},
                  "t": {"integerValue": str(int(time.time() * 1000))}}
        if lang == "de":
            fields["mp3"] = {"stringValue": tts(text, VOICE["de"])}
            fields["mp3s"] = {"stringValue": tts(text, VOICE["de2"])}
        else:
            fields["mp3"] = {"stringValue": tts(spoken_ru(text), VOICE["ru"])}
        http("PATCH", f"{BASE}/audio/{i}", {"fields": fields}, token)
        done += 1; print("recorded:", text)
    except Exception as e:
        failed.append(f"{text}: {e}")
print(f"Recorded {done} of {len(todo)}" + (f"; failed: {failed}" if failed else ""))
if failed: sys.exit(1)
