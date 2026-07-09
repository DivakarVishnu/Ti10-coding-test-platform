import requests
from config import Config

JUDGE0_URL = Config.JUDGE0_URL.rstrip("/")
RAPIDAPI_KEY = Config.RAPIDAPI_KEY
RAPIDAPI_HOST = Config.RAPIDAPI_HOST

# Common Judge0 CE language ids
LANGUAGES = {
    71: "Python 3",
    62: "Java",
    54: "C++ (GCC 9.2.0)",
    50: "C (GCC 9.2.0)",
    63: "JavaScript (Node.js)",
}


def _headers():
    headers = {"Content-Type": "application/json"}
    if RAPIDAPI_KEY:
        headers["X-RapidAPI-Key"] = RAPIDAPI_KEY
        headers["X-RapidAPI-Host"] = RAPIDAPI_HOST
    return headers


def run_code(source_code, language_id, stdin="", expected_output=None,
             cpu_time_limit=2.0, memory_limit=128000, timeout=25):
    """
    Submits code to Judge0 in synchronous mode (wait=true) and returns the
    parsed JSON result, or {"error": "..."} on failure.
    """
    payload = {
        "source_code": source_code,
        "language_id": language_id,
        "stdin": stdin or "",
        "cpu_time_limit": cpu_time_limit,
        "memory_limit": memory_limit,
    }
    if expected_output is not None:
        payload["expected_output"] = expected_output

    url = f"{JUDGE0_URL}/submissions/"
    params = {"base64_encoded": "false", "wait": "true"}

    try:
        resp = requests.post(url, params=params, json=payload, headers=_headers(), timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        return {"error": f"Judge0 request failed: {e}"}


def get_languages():
    """Optionally fetch live language list from Judge0. Falls back to LANGUAGES."""
    try:
        resp = requests.get(f"{JUDGE0_URL}/languages", headers=_headers(), timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return {item["id"]: item["name"] for item in data}
    except requests.RequestException:
        return LANGUAGES
