"""Passive CDP source capture for one explicitly selected Android WebView.

REA currently rejects this WebView's endpoint response. This fallback records
ordinary CDP evidence; it does not navigate or evaluate application code.
"""
import argparse
import hashlib
import json
import time
from pathlib import Path
from urllib.parse import urlsplit

import websocket


def capture(url, output):
    output.mkdir(parents=True, exist_ok=True)
    socket = websocket.create_connection(url, timeout=3, suppress_origin=True)
    counter = 0
    scripts, events, requests = {}, [], {}

    def send(method, params=None):
        nonlocal counter
        counter += 1
        socket.send(json.dumps({'id': counter, 'method': method, 'params': params or {}}))
        return counter

    try:
        send('Debugger.enable')
        send('Network.enable', {'maxPostDataSize': 0})
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            try: message = json.loads(socket.recv())
            except websocket.WebSocketTimeoutException: break
            if message.get('method') == 'Debugger.scriptParsed':
                item = message['params']
                origin = urlsplit(item.get('url', '')).hostname or ''
                if origin.endswith('.starbucks.com.cn'):
                    scripts[item['scriptId']] = item
            elif message.get('method', '').startswith('Network.'):
                # Retain method and URL only, never credentials or body contents.
                item = message.get('params', {})
                req = item.get('request', {})
                if req:
                    events.append({'method': req.get('method'), 'url': req.get('url')})
        for script_id in scripts:
            requests[send('Debugger.getScriptSource', {'scriptId': script_id})] = script_id
        pending = set(requests)
        deadline = time.monotonic() + 15
        manifest = []
        while pending and time.monotonic() < deadline:
            try: message = json.loads(socket.recv())
            except websocket.WebSocketTimeoutException: break
            if message.get('id') not in pending: continue
            call_id = message['id']
            pending.remove(call_id)
            script_id = requests[call_id]
            source = message.get('result', {}).get('scriptSource')
            if source is None: continue
            encoded = source.encode('utf-8')
            name = f'script-{script_id}.js'
            (output / name).write_bytes(encoded)
            manifest.append({'url': scripts[script_id]['url'], 'file': name,
                             'bytes': len(encoded), 'sha256': hashlib.sha256(encoded).hexdigest()})
        (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        (output / 'network-urls.json').write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding='utf-8')
        return manifest
    finally:
        socket.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('websocket_url')
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    print(json.dumps(capture(args.websocket_url, args.output), ensure_ascii=False, indent=2))
