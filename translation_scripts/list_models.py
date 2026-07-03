import os, json, urllib.request
from dotenv import load_dotenv
load_dotenv()

key = os.environ.get("GEMINI_API_KEY", "")
url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
with urllib.request.urlopen(url) as r:
    models = json.loads(r.read())

for m in models.get("models", []):
    methods = m.get("supportedGenerationMethods", [])
    if "generateContent" in methods:
        print(m["name"])
