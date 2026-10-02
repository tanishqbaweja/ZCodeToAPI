import argparse
import base64
import getpass
import hashlib
import json
import os
import platform
import queue
import sys
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import requests
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


APP_VERSION = "3.14.4"
ORIGIN = "https://zcode.z.ai"
CONFIG_URL = f"{ORIGIN}/api/v1/client/configs"
MODEL_URL = f"{ORIGIN}/api/v1/zcode-plan/anthropic/v1/messages"
BALANCE_URL = f"{ORIGIN}/api/v1/zcode-plan/billing/balance"
ALIYUN_SDK_URL = "https://o.alicdn.com/captcha-frontend/aliyunCaptcha/AliyunCaptcha.js"
REQUEST_TEMPLATE_PATH = Path(__file__).resolve().with_name("start_plan_request_template.json")
DIAGNOSTICS_PATH = Path(__file__).resolve().with_name("zcode-recovery-logs") / "captcha-verifier-latest.jsonl"


def credentials_path() -> Path:
    explicit = os.environ.get("ZCODE_CREDENTIALS_FILE", "").strip()
    if explicit:
        return Path(explicit)
    root = Path(os.environ.get("ZCODE_HOME", str(Path.home() / ".zcode")))
    return root / "v2" / "credentials.json"


def credential_secret() -> str:
    explicit = os.environ.get("ZCODE_CREDENTIAL_SECRET", "").strip()
    if explicit:
        return explicit
    return f"zcode-credential-fallback:win32:{Path.home()}:{getpass.getuser()}"


def _decode_base64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def decrypt_credential(value: str) -> str:
    if not value.startswith("enc:v1:"):
        return value
    parts = value[len("enc:v1:") :].split(".")
    if len(parts) != 3:
        raise RuntimeError("Invalid encrypted ZCode credential format")
    iv = _decode_base64url(parts[0])
    tag = _decode_base64url(parts[1])
    ciphertext = _decode_base64url(parts[2])
    key = hashlib.sha256(credential_secret().encode("utf-8")).digest()
    return AESGCM(key).decrypt(iv, ciphertext + tag, None).decode("utf-8")


def load_start_plan_token() -> str:
    env_token = (os.environ.get("ZCODE_START_PLAN_TOKEN") or os.environ.get("ZCODE_JWT_TOKEN") or "").strip()
    if env_token:
        return env_token

    path = credentials_path()
    if not path.exists():
        return ""
    store = json.loads(path.read_text(encoding="utf-8"))
    raw = store.get("zcodejwttoken")
    if not isinstance(raw, str):
        return ""
    return decrypt_credential(raw).strip()


def local_device_mid() -> str:
    explicit = os.environ.get("ZCODE_DEVICE_MID", "").strip()
    if explicit:
        return explicit
    path = Path.home() / ".zcode" / "v2" / "telemetry-state.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8")).get("deviceMid", "")
        return value.strip() if isinstance(value, str) else ""
    except Exception:
        return ""


def local_account_uuid() -> str:
    path = credentials_path()
    try:
        store = json.loads(path.read_text(encoding="utf-8"))
        raw = store.get("oauth:zai:user_info")
        if not isinstance(raw, str):
            return ""
        decoded = decrypt_credential(raw)
        profile = json.loads(decoded)
        value = profile.get("user_id")
        return value.strip() if isinstance(value, str) else ""
    except Exception:
        return ""


def load_request_template() -> dict:
    if not REQUEST_TEMPLATE_PATH.exists():
        raise RuntimeError(f"Missing request template: {REQUEST_TEMPLATE_PATH}")
    data = json.loads(REQUEST_TEMPLATE_PATH.read_text(encoding="utf-8"))
    body = data.get("bodyTemplate")
    if not isinstance(body, dict):
        raise RuntimeError("Start Plan request template is invalid")
    # JSON round-trip gives us an isolated mutable copy. The checked-in
    # template intentionally contains no machine/user-specific paths; restore
    # the local workspace path only in memory at runtime.
    rendered = json.loads(json.dumps(body))
    local_workspace = str(Path.home() / ".zcode" / "workspace" / "default")

    def replace_placeholders(value):
        if isinstance(value, str):
            return value.replace("__ZCODE_PRIMARY_WORKING_DIRECTORY__", local_workspace)
        if isinstance(value, list):
            return [replace_placeholders(item) for item in value]
        if isinstance(value, dict):
            return {key: replace_placeholders(item) for key, item in value.items()}
        return value

    return replace_placeholders(rendered)


def base_headers(token: str, *, model_request: bool = False) -> dict[str, str]:
    headers = {
        "User-Agent": f"ZCode/{APP_VERSION}",
        "HTTP-Referer": ORIGIN,
        "X-Title": "Z Code@electron",
        "X-ZCode-App-Version": APP_VERSION,
        "X-Platform": "win32-x64",
        "X-Release-Channel": "production",
        "X-Client-Language": "en-IN",
        "X-Client-Timezone": "Asia/Calcutta",
        "X-Os-Category": "windows",
        "X-Os-Version": platform.version(),
        "X-ZCode-Agent": "glm",
        "Accept": "application/json",
        "x-request-id": str(uuid.uuid4()),
    }
    if model_request:
        # The packaged AccountProviderRequestAuthService projects the Start
        # Plan zcodeJwtToken as requestAuth.apiKey. The bundled Anthropic
        # path sends it as x-api-key and also injects Authorization: Bearer.
        headers["x-api-key"] = token
        headers["Authorization"] = f"Bearer {token}"
    else:
        headers["Authorization"] = f"Bearer {token}"
    device_mid = local_device_mid()
    if device_mid and not model_request:
        headers["X-Device-Mid"] = device_mid
    return headers


def uuid7_like() -> str:
    # RFC 9562 UUIDv7: 48-bit Unix epoch milliseconds + random payload.
    ms = int(time.time() * 1000) & ((1 << 48) - 1)
    rand_a = int.from_bytes(os.urandom(2), "big") & 0x0FFF
    rand_b = int.from_bytes(os.urandom(8), "big") & ((1 << 62) - 1)
    value = (ms << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    return str(uuid.UUID(int=value))


def fetch_captcha_config(token: str) -> dict:
    response = requests.get(
        CONFIG_URL,
        params={"app_version": APP_VERSION, "platform": "win32-x64"},
        headers=base_headers(token),
        timeout=20,
    )
    response.raise_for_status()
    body = response.json()
    captcha = ((body.get("data") or {}).get("configs") or {}).get("captcha")
    if not isinstance(captcha, dict):
        raise RuntimeError("Server did not return data.configs.captcha")
    required = ["region", "prefix", "sceneId"]
    missing = [key for key in required if not str(captcha.get(key, "")).strip()]
    if missing:
        raise RuntimeError(f"Captcha config is missing: {', '.join(missing)}")
    return captcha


def fetch_billing_balance(token: str) -> dict:
    response = requests.get(
        BALANCE_URL,
        params={"app_version": APP_VERSION},
        headers=base_headers(token),
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


def summarize_billing_balance(body: dict) -> dict:
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict):
        data = {}
    plans = data.get("plans") if isinstance(data.get("plans"), list) else []
    balances = data.get("balances") if isinstance(data.get("balances"), list) else []
    entitlement_meta: dict[tuple[str, str], dict] = {}
    for plan in plans:
        if not isinstance(plan, dict):
            continue
        plan_id = str(plan.get("plan_id") or "")
        for entitlement in plan.get("entitlements") or []:
            if not isinstance(entitlement, dict):
                continue
            entitlement_id = str(entitlement.get("entitlement_id") or "")
            entitlement_meta[(plan_id, entitlement_id)] = entitlement
    summarized = []
    for balance in balances:
        if not isinstance(balance, dict):
            continue
        plan_id = str(balance.get("plan_id") or "")
        entitlement_id = str(balance.get("entitlement_id") or "")
        entitlement = entitlement_meta.get((plan_id, entitlement_id), {})
        total = int(balance.get("total_units") or 0)
        remaining = int(balance.get("remaining_units") or 0)
        used = int(balance.get("used_units") or max(total - remaining, 0))
        summarized.append(
            {
                "show_name": balance.get("show_name"),
                "capabilities": balance.get("capabilities") or [],
                "plan_id": balance.get("plan_id"),
                "entitlement_id": balance.get("entitlement_id"),
                "period": entitlement.get("period"),
                "total_units": total,
                "used_units": used,
                "remaining_units": remaining,
                "available_units": int(balance.get("available_units") or remaining),
                "percentage_remaining": round((remaining / total) * 100, 2) if total else None,
                "period_start": balance.get("period_start"),
                "period_end": balance.get("period_end"),
                "expires_at": balance.get("expires_at"),
                "priority": balance.get("priority"),
                "plan_priority": balance.get("plan_priority"),
            }
        )
    return {
        "server_time": data.get("server_time"),
        "plans": [
            {
                "name": plan.get("name"),
                "plan_id": plan.get("plan_id"),
                "status": plan.get("status"),
                "starts_at": plan.get("starts_at"),
                "ends_at": plan.get("ends_at"),
            }
            for plan in plans
            if isinstance(plan, dict)
        ],
        "balances": summarized,
        "raw": body,
    }


def normalize_thinking_level(value: str | None) -> str:
    raw = (value or os.environ.get("ZCODE_THINKING_LEVEL") or "max").strip().lower()
    aliases = {
        "none": "low",
        "minimal": "low",
        "medium": "high",
        "normal": "high",
        "default": "max",
        "xhigh": "max",
        "ultra": "max",
        "persistent": "max",
        "maximum": "max",
    }
    raw = aliases.get(raw, raw)
    return raw if raw in {"low", "high", "max"} else "max"


def apply_thinking_level(body: dict, level: str | None) -> str:
    normalized = normalize_thinking_level(level)
    body["thinking"] = {"type": "enabled"}
    body["output_config"] = {"effort": normalized}
    return normalized


def verifier_html(config: dict) -> str:
    config_json = json.dumps(
        {
            "region": str(config["region"]),
            "prefix": str(config["prefix"]),
            "sceneId": str(config["sceneId"]),
        }
    )
    sdk_json = json.dumps(ALIYUN_SDK_URL)
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Start Plan verification</title>
  <style>
    html, body {{ margin: 0; min-height: 100%; font-family: system-ui, sans-serif; background: #111; color: #eee; }}
    main {{ max-width: 720px; margin: 48px auto; padding: 24px; }}
    #status {{ white-space: pre-wrap; opacity: .85; }}
    #zcode-aliyun-captcha-container {{ position: fixed; left: 0; top: 0; z-index: 2147483647; height: 0; width: 0; overflow: visible; }}
    #zcode-aliyun-captcha-element {{ position: absolute; left: 0; top: 0; height: 0; width: 0; overflow: visible; }}
    #zcode-aliyun-captcha-button {{ position: fixed; left: 50%; top: 50%; height: 1px; width: 1px; transform: translate(-50%, -50%); border: 0; padding: 0; opacity: 0; }}
    #manual-button {{ margin-top: 18px; padding: 10px 16px; }}
  </style>
</head>
<body>
<main>
  <h2>Start Plan verification</h2>
  <p id="status">Loading official Aliyun verification SDKâ€¦</p>
  <div id="zcode-aliyun-captcha-container" aria-hidden="true">
    <div id="zcode-aliyun-captcha-element"></div>
    <button id="zcode-aliyun-captcha-button" type="button" tabindex="-1" aria-hidden="true">Verify</button>
  </div>
  <button id="manual-button" type="button">Show / retry challenge</button>
</main>
<script>
(() => {{
  const cfg = {config_json};
  const status = document.getElementById('status');
  const button = document.getElementById('zcode-aliyun-captcha-button');
  const manualButton = document.getElementById('manual-button');
  window.__captchaResult = null;
  window.__captchaInstance = null;
  window.__sdkLoadedAt = 0;
  window.AliyunCaptchaConfig = {{ region: cfg.region, prefix: cfg.prefix }};

  const diagnostic = async (event, value = null) => {{
    const v = value && typeof value === 'object' ? value : {{}};
    const payload = {{
      event,
      origin: location.origin,
      href: location.href,
      visibilityState: document.visibilityState,
      verifyCode: v.verifyCode || v.VerifyCode || null,
      success: typeof v.success === 'boolean' ? v.success : null,
      verifyResult: typeof v.verifyResult === 'boolean' ? v.verifyResult : null,
      certifyId: typeof v.certifyId === 'string' ? v.certifyId : null,
      aliyunErrorCode: v.Code || v.code || v.errorCode || v.ErrorCode || null,
      aliyunErrorMessage: v.Message || v.message || v.errorMessage || v.ErrorMessage || v.msg || null,
      requestId: v.RequestId || v.requestId || v.requestID || null,
      statusCode: v.StatusCode || v.statusCode || v.status || null,
      hasCaptchaVerifyParam: typeof (v.captchaVerifyParam || v.CaptchaVerifyParam) === 'string',
      raw: value ? JSON.parse(JSON.stringify(value, Object.getOwnPropertyNames(value))) : null
    }};
    try {{
      await fetch('/diagnostic', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify(payload)
      }});
    }} catch (_) {{}}
  }};

  const finish = async (value, source) => {{
    if (typeof value !== 'string' || !value.trim() || window.__captchaResult) return;
    window.__captchaResult = {{ ok: true, value: value.trim(), source }};
    diagnostic('verification.finish', {{}});
    status.textContent = 'Verification completed. Returning to Pythonâ€¦';
    try {{
      const response = await fetch('/verification-result', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ value: value.trim(), source }})
      }});
      if (!response.ok) throw new Error('Python callback rejected verification result');
      status.textContent = 'Verification received. You can close this tab.';
    }} catch (e) {{
      status.textContent = 'Verification succeeded, but the Python callback failed: ' + String(e);
    }}
  }};

  const terminalError = async (message, value = null) => {{
    if (window.__captchaResult) return;
    window.__captchaResult = {{ ok: false, error: message }};
    const v = value && typeof value === 'object' ? value : {{}};
    status.textContent = message;
    try {{
      await fetch('/verification-error', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{
          error: message,
          verifyCode: v.verifyCode || v.VerifyCode || null,
          success: typeof v.success === 'boolean' ? v.success : null,
          verifyResult: typeof v.verifyResult === 'boolean' ? v.verifyResult : null,
          aliyunErrorCode: v.Code || v.code || v.errorCode || v.ErrorCode || null,
          aliyunErrorMessage: v.Message || v.message || v.errorMessage || v.ErrorMessage || v.msg || null,
          requestId: v.RequestId || v.requestId || v.requestID || null,
          statusCode: v.StatusCode || v.statusCode || v.status || null
        }})
      }});
    }} catch (_) {{}}
  }};

  const failResult = (err) => {{
    if (!err || typeof err !== 'object') return false;
    const code = err.verifyCode || err.VerifyCode;
    const param = err.captchaVerifyParam || err.CaptchaVerifyParam;
    const terminalPass = (err.success === true && err.verifyResult === true) || code === 'T006';
    if (terminalPass && typeof param === 'string' && param.trim()) {{
      finish(param, 'terminal-fail-callback');
      return true;
    }}
    return false;
  }};

  const script = document.createElement('script');
  script.src = {sdk_json};
  script.async = true;
  script.onerror = () => {{
    diagnostic('sdk.script_error');
    terminalError('Failed to load AliyunCaptcha.js');
  }};
  manualButton.addEventListener('click', () => {{
    diagnostic('manual.retry_clicked');
    try {{
      if (window.__captchaInstance && typeof window.__captchaInstance.show === 'function') {{
        window.__captchaInstance.show();
      }} else {{
        button.click();
      }}
    }} catch (e) {{
      diagnostic('manual.retry_error', {{ message: String(e) }});
    }}
  }});

  script.onload = () => {{
    window.__sdkLoadedAt = Date.now();
    diagnostic('sdk.script_loaded');
    status.textContent = 'Initializing verificationâ€¦';
    if (typeof window.initAliyunCaptcha !== 'function') {{
      terminalError('initAliyunCaptcha is unavailable');
      return;
    }}
    try {{
      window.initAliyunCaptcha({{
        SceneId: cfg.sceneId,
        mode: 'popup',
        language: 'en',
        showErrorTip: false,
        element: '#zcode-aliyun-captcha-element',
        button: '#zcode-aliyun-captcha-button',
        getInstance(instance) {{
          diagnostic('sdk.getInstance');
          window.__captchaInstance = instance;
          status.textContent = 'Running verificationâ€¦';
          const elapsedSinceSdkLoad = Date.now() - window.__sdkLoadedAt;
          const triggerDelay = Math.max(0, 2000 - elapsedSinceSdkLoad);
          setTimeout(() => {{
            try {{
              if (typeof instance.startTracelessVerification === 'function') {{
                instance.startTracelessVerification();
              }} else {{
                button.click();
              }}
            }} catch (e) {{
              terminalError('Failed to start verification: ' + String(e));
            }}
          }}, triggerDelay);
        }},
        success(param) {{
          diagnostic('sdk.success');
          finish(param, 'success');
        }},
        fail(err) {{
          diagnostic('sdk.fail', err);
          if (failResult(err)) return;
          const code = err && (err.verifyCode || err.VerifyCode);
          const terminalPass = err && ((err.success === true && err.verifyResult === true) || code === 'T006');
          if (terminalPass) {{
            // ZCode waits here because Aliyun may send the actual verification
            // parameter in a later success callback for the same attempt.
            status.textContent = 'Verification passed; waiting for the final verification resultâ€¦';
            return;
          }}
          const needsInteractive = err && err.success === true && err.verifyResult === false && (!code || code === 'F015');
          if (needsInteractive) {{
            // ZCode normally finishes invisibly/tracelessly. This branch only
            // happens if Aliyun upgrades the same attempt to an interactive
            // challenge. Use the hidden SDK button just like the packaged app.
            status.textContent = 'Aliyun upgraded this invisible check to an interactive challenge. Complete it if it appears.';
            try {{ button.click(); }} catch (e) {{ diagnostic('interactive.click_failed', {{ message: String(e) }}); }}
            return;
          }}
          if (code === 'F008') {{
            terminalError('Verification data was already submitted (F008). Start a fresh attempt.', err);
            return;
          }}
          status.textContent = 'Verification is waiting for completionâ€¦ (code: ' + (code || 'none') + ')';
        }},
        onError(err) {{
          diagnostic('sdk.onError', err);
          terminalError('Aliyun verifier error', err);
        }}
      }});
    }} catch (e) {{
      terminalError('Aliyun verifier initialization failed: ' + String(e));
    }}
  }};
  document.head.appendChild(script);
}})();
</script>
</body>
</html>"""


class _VerifierHandler(BaseHTTPRequestHandler):
    page_bytes = b""
    results: queue.Queue | None = None

    def do_GET(self):
        if self.path not in ("/", "/index.html"):
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(self.page_bytes)))
        self.end_headers()
        self.wfile.write(self.page_bytes)

    def do_POST(self):
        if self.path == "/diagnostic":
            try:
                size = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(size).decode("utf-8"))
                DIAGNOSTICS_PATH.parent.mkdir(parents=True, exist_ok=True)
                record = {"timestamp": time.time(), **body}
                with DIAGNOSTICS_PATH.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                print(
                    "CAPTCHA diagnostic: "
                    f"event={record.get('event')} code={record.get('verifyCode')} "
                    f"success={record.get('success')} verifyResult={record.get('verifyResult')} "
                    f"origin={record.get('origin')}"
                )
                payload = b'{"ok":true}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except Exception as exc:
                self.send_error(400, str(exc))
            return
        if self.path == "/verification-error":
            try:
                size = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(size).decode("utf-8"))
                error = str(body.get("error") or "Aliyun verification failed")
                result = {"ok": False, "error": error, "diagnostic": body}
                if self.results is not None and self.results.empty():
                    self.results.put_nowait(result)
                payload = b'{"ok":true}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except Exception as exc:
                self.send_error(400, str(exc))
            return
        if self.path != "/verification-result":
            self.send_response(404)
            self.end_headers()
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(size).decode("utf-8"))
            value = body.get("value")
            source = body.get("source")
            if not isinstance(value, str) or not value.strip():
                raise ValueError("Missing verification value")
            result = {"ok": True, "value": value.strip(), "source": source}
            if self.results is not None and self.results.empty():
                self.results.put_nowait(result)
            payload = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except Exception as exc:
            payload = json.dumps({"ok": False, "error": str(exc)}).encode("utf-8")
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    def log_message(self, *_args):
        pass


def obtain_fresh_verification(config: dict, *, timeout_seconds: float) -> str:
    try:
        DIAGNOSTICS_PATH.unlink()
    except FileNotFoundError:
        pass
    html_text = verifier_html(config)
    debug_html = DIAGNOSTICS_PATH.with_name("captcha-verifier-latest.html")
    debug_html.parent.mkdir(parents=True, exist_ok=True)
    debug_html.write_text(html_text, encoding="utf-8")
    _VerifierHandler.page_bytes = html_text.encode("utf-8")
    result_queue: queue.Queue = queue.Queue(maxsize=1)
    _VerifierHandler.results = result_queue
    server = ThreadingHTTPServer(("127.0.0.1", 0), _VerifierHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}/"

    try:
        print(f"Opening official verifier: {url}")
        webbrowser.open(url, new=1, autoraise=True)
        try:
            result = result_queue.get(timeout=timeout_seconds)
        except queue.Empty as exc:
            raise RuntimeError(
                "Verification timed out. Complete the Aliyun challenge in the opened browser tab."
            ) from exc
    finally:
        server.shutdown()
        server.server_close()
        _VerifierHandler.results = None

    if not isinstance(result, dict) or not result.get("ok"):
        error = result.get("error") if isinstance(result, dict) else repr(result)
        raise RuntimeError(f"Aliyun verification failed: {error}")
    value = str(result.get("value", "")).strip()
    if not value:
        raise RuntimeError("Aliyun verification completed without a verification value")
    print(f"Fresh verification obtained ({len(value)} characters; source={result.get('source')}).")
    return value


def model_headers(token: str, verification: str | None, region: str, session_id: str) -> dict[str, str]:
    headers = base_headers(token, model_request=True)
    headers.update(
        {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "User-Agent": f"ZCode/{APP_VERSION} ai-sdk/anthropic/3.0.81",
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "mid-conversation-system-2026-04-07",
            "x-zcode-trace-id": str(uuid.uuid4()),
            "x-zcode-session-type": "main",
            "x-query-id": uuid7_like(),
            "x-session-id": session_id,
        }
    )
    if verification:
        headers["X-Aliyun-Captcha-Verify-Param"] = verification
        headers["X-Aliyun-Captcha-Verify-Region"] = region
    return headers


def parse_model_response(response: requests.Response) -> tuple[str, str, dict | None]:
    content_type = (response.headers.get("content-type") or "").lower()
    if "text/event-stream" in content_type:
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        usage = None
        for raw in response.iter_lines(decode_unicode=True):
            if not raw or not raw.startswith("data:"):
                continue
            payload = raw[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                event = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "error":
                raise RuntimeError(json.dumps(event.get("error", event), ensure_ascii=False))
            if event.get("type") == "content_block_delta":
                delta = event.get("delta") or {}
                if delta.get("type") == "text_delta" and isinstance(delta.get("text"), str):
                    text_parts.append(delta["text"])
                elif delta.get("type") == "thinking_delta" and isinstance(delta.get("thinking"), str):
                    reasoning_parts.append(delta["thinking"])
            current_usage = (event.get("message") or {}).get("usage") or event.get("usage")
            if current_usage:
                usage = current_usage
        return "".join(text_parts), "".join(reasoning_parts), usage

    body = response.json()
    text_parts = []
    reasoning_parts = []
    for block in body.get("content") or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            text_parts.append(block["text"])
        elif block.get("type") == "thinking" and isinstance(block.get("thinking"), str):
            reasoning_parts.append(block["thinking"])
    return "".join(text_parts), "".join(reasoning_parts), body.get("usage")


def send_start_plan_message(
    token: str,
    config: dict,
    verification: str | None,
    prompt: str,
    model: str,
    timeout: float,
    system_override: str | None = None,
    thinking_level: str | None = None,
):
    session_id = str(uuid.uuid4())
    device_mid = local_device_mid()
    account_uuid = local_account_uuid()
    metadata_user_id = json.dumps(
        {"device_id": device_mid, "account_uuid": account_uuid, "session_id": session_id},
        separators=(",", ":"),
    )
    body = load_request_template()
    if system_override is not None:
        system_blocks = body.get("system")
        if isinstance(system_blocks, list) and system_blocks and isinstance(system_blocks[0], dict):
            system_blocks[0]["text"] = system_override
            system_blocks[0].setdefault("cache_control", {"type": "ephemeral"})
        else:
            body["system"] = [
                {
                    "type": "text",
                    "text": system_override,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
    body["model"] = model
    body["stream"] = True
    apply_thinking_level(body, thinking_level)
    body["metadata"] = {"user_id": metadata_user_id}
    body["messages"] = [
        {"role": "user", "content": [{"type": "text", "text": prompt}]}
    ]
    return requests.post(
        MODEL_URL,
        headers=model_headers(token, verification, str(config["region"]), session_id),
        json=body,
        timeout=timeout,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Standalone Start Plan smoke test: official Aliyun verification -> direct model endpoint."
    )
    parser.add_argument("prompt", nargs="?", default="Hello")
    parser.add_argument("--model", default="GLM-5.3-Flash")
    parser.add_argument("--verification-timeout", type=float, default=120.0)
    parser.add_argument("--request-timeout", type=float, default=60.0)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--balance", action="store_true", help="Fetch and print current Start Plan billing/quota balance.")
    parser.add_argument("--thinking-level", choices=["low", "high", "max"], default=None)
    parser.add_argument("--system", help="Override the client-sent system prompt for this request.")
    parser.add_argument("--system-file", help="Load the client-sent system prompt override from a UTF-8 text file.")
    parser.add_argument("--region")
    parser.add_argument("--prefix")
    parser.add_argument("--scene-id")
    args = parser.parse_args()

    if args.system and args.system_file:
        print("Use either --system or --system-file, not both.", file=sys.stder)
        return 2
    system_override = None
    if args.system_file:
        system_override = Path(args.system_file).read_text(encoding="utf-8").strip()
    elif args.system:
        system_override = args.system.strip()
    if system_override == "":
        system_override = None

    token = load_start_plan_token()
    explicit_config = args.region and args.prefix and args.scene_id

    if not token and not explicit_config:
        print(
            "A Start Plan JWT is required. Set ZCODE_START_PLAN_TOKEN or keep the existing "
            "~/.zcode/v2/credentials.json available.",
            file=sys.stderr,
        )
        return 2

    if args.balance:
        balance = summarize_billing_balance(fetch_billing_balance(token))
        print(json.dumps(balance, indent=2, ensure_ascii=False))
        return 0

    if explicit_config:
        config = {
            "enabled": True,
            "region": args.region,
            "prefix": args.prefix,
            "sceneId": args.scene_id,
        }
    else:
        config = fetch_captcha_config(token)

    print(
        "Captcha config: "
        f"region={config['region']} prefix={config['prefix']} sceneId={config['sceneId']} "
        f"enabled={config.get('enabled')} skip_model_request={config.get('skip_model_request')}"
    )

    skip_model_request = config.get("skip_model_request") is True or config.get("enabled") is False
    if skip_model_request:
        verification = None
        print("Current server config skips CAPTCHA for model requests; no verifier is needed.")
        if args.verify_only:
            print("Verification-only test skipped because model CAPTCHA is disabled by server config.")
            return 0
    else:
        verification = obtain_fresh_verification(
            config,
            timeout_seconds=args.verification_timeout,
        )
        if args.verify_only:
            print("Verification-only test succeeded. Token value was intentionally not printed.")
            return 0
    if not token:
        print("A Start Plan JWT is required for the model request.", file=sys.stderr)
        return 2

    started = time.perf_counter()
    response = send_start_plan_message(
        token,
        config,
        verification,
        args.prompt,
        args.model,
        args.request_timeout,
        system_override=system_override,
        thinking_level=args.thinking_level,
    )
    print(f"POST {MODEL_URL}")
    print(f"HTTP {response.status_code} {response.reason}")
    if not response.ok:
        try:
            print(json.dumps(response.json(), indent=2, ensure_ascii=False))
        except ValueError:
            print(response.text)
        return 1

    text, reasoning, usage = parse_model_response(response)
    print("Response:")
    print(text)
    if reasoning:
        print("\nReasoning:")
        print(reasoning)
    if usage:
        print("\nUsage:")
        print(json.dumps(usage, indent=2, ensure_ascii=False))
    print(f"\nElapsed model request: {time.perf_counter() - started:.2f}s")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Cancelled.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)



