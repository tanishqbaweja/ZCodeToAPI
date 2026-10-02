# App Integration Notes

## Endpoint

The direct model endpoint used by the current client is:

```text
https://zcode.z.ai/api/v1/zcode-plan/anthropic/v1/messages
```

The request still goes through ZCode's Start Plan backend. It is not the raw Z.ai GLM API.

## Runtime flow

```text
1. Load user-owned ZCode auth
2. Fetch live client config from ZCode
3. Check CAPTCHA/model-request config
4. Use stock Start Plan request template
5. Send the user's message to the Start Plan Anthropic-compatible endpoint
6. Parse SSE/JSON response
```

## Environment variables

```bat
ZCODE_CREDENTIALS_FILE     Optional path to credentials.json
ZCODE_CREDENTIAL_SECRET   Optional custom decryption secret if applicable
ZCODE_START_PLAN_TOKEN    Optional process-only token override
ZCODE_JWT_TOKEN           Alias token env supported by the script
ZCODE_DEVICE_MID          Optional device id override
```

## Recommended app architecture

For a desktop app:

```text
Your app
  -> account/profile selector
  -> launches worker process under selected account context
  -> worker imports zcode_direct_client.py
  -> worker sends accepted stock-template requests
  -> app displays streamed/final response
```

For a server app:

```text
Avoid centralizing user credentials unless you have explicit authorization and secure storage.
Prefer local-user execution or user-provided process-only credentials.
Never store raw credential files or tokens in repo.
```

## Multi-account design

Prefer:

```text
one OS user/profile = one ZCode credential store
```

or:

```text
one isolated worker process = one process-only token / credential context
```

Avoid mixing multiple accounts in one global process unless you build clear account isolation and never log secrets.

## Known limitation

The backend accepted requests that preserve the stock ZCode system/template. Tests that replaced the system prompt with a custom file returned:

```text
HTTP 405
code: 3012
msg: request has been blocked due to unusual activity.
```

So the current integration strategy is:

```text
Preserve system/template.
Customize with user messages/history.
```

## Smoke tests

```bat
Open a terminal in this folder.

py -m py_compile zcode_direct_client.py
py zcode_direct_client.py "Reply only OK."
py zcode_direct_client.py "Hello how are you"
```

## Output files from earlier proof runs

Useful proof/log files remain in:

```text
../zcode-recovery-logs
```

Latest important ones:

```text
093-current-direct-smoke.txt
094-current-direct-hello-how-are-you.txt
098-direct-stock-after-project-files.txt
100-two-user-message-identity-test.txt
```
