# Network analysis (mitmproxy)

## CA certificate (already generated)

`~/.mitmproxy/mitmproxy-ca-cert.pem` — install on target device/system to
intercept TLS.

## Capture modes

```sh
mitmdump -w flows.mitm                       # record to file, :8080
mitmdump -w flows.mitm --mode transparent    # transparent/gateway mode
mitmweb --web-port 8081                      # browser UI
mitmproxy --set save_stream_file=flows.mitm  # interactive
```

Point targets at the proxy: `http_proxy=http://127.0.0.1:8080/` and
`https_proxy=...` plus the CA cert.

## Scripted processing

```sh
mitmdump -r flows.mitm -n -s script.py
```

```python
# script.py
from mitmproxy import http

def response(flow: http.HTTPFlow) -> None:
    if "api" in flow.request.pretty_host:
        print(flow.request.pretty_url, len(flow.response.content))
```

## With REA

`rea compare` on captured evidence bundles is the canonical way to diff two
captures (e.g. original vs rebuilt client behavior).
