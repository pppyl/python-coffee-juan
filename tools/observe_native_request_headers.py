"""One local-only development request via the app's documented JS bridge.

Does not contact a Starbucks SMS endpoint. Authentication-token attachment is
disabled. Captured headers remain in ignored local evidence, never app config.
"""
import argparse
import http.server
import json
import subprocess
import threading
import time
from pathlib import Path

import websocket


def main(args):
    captured = {}
    complete = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get('Content-Length', '0')))
            captured.update(path=self.path, headers=dict(self.headers), body=raw.decode('utf-8', 'replace'))
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"success":true,"data":{}}')
            complete.set()
        def log_message(self, *args): pass

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    port = server.server_port
    threading.Thread(target=server.serve_forever, daemon=True).start()
    adb = args.adb
    subprocess.run([adb, '-s', args.device, 'reverse', f'tcp:{port}', f'tcp:{port}'], check=True, capture_output=True)
    socket = None
    try:
        socket = websocket.create_connection(args.websocket_url, timeout=12, suppress_origin=True)
        params = {'url': f'http://127.0.0.1:{port}/app-bff-api/default/login/sms/send',
                  'method': 'post', 'token': False, 'signature': True, 'needBase': False,
                  'deviceInfo': False, 'header': {'Content-Type': 'application/json'},
                  'data': {'phoneNumber': '', 'template': '', 'token': '', 'device': {}}}
        request = {'funcName': 'request', 'parameters': params}
        expression = 'new Promise(resolve => { const b=window.WebViewJavascriptBridge; if(!b) return resolve({error:"bridge unavailable"}); b.callHandler("Network",' + json.dumps(request) + ',r=>resolve(r)); setTimeout(()=>resolve({error:"bridge timeout"}),10000); })'
        socket.send(json.dumps({'id': 1, 'method': 'Runtime.evaluate', 'params': {'expression': expression, 'awaitPromise': True, 'returnByValue': True}}))
        deadline = time.monotonic() + 15
        callback = None
        while time.monotonic() < deadline:
            value = json.loads(socket.recv())
            if value.get('id') == 1:
                callback = value.get('result', {}).get('result', {}).get('value')
                break
        complete.wait(1)
        destination = Path('evidence/native-request-observation-private.json')
        destination.write_text(json.dumps({'capture': captured, 'callback': callback}, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'captured': bool(captured), 'header_names': list(captured.get('headers', {})), 'callback_type': type(callback).__name__}))
    finally:
        if socket: socket.close()
        subprocess.run([adb, '-s', args.device, 'reverse', '--remove', f'tcp:{port}'], capture_output=True)
        server.shutdown()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--adb', default='adb')
    parser.add_argument('--device', required=True)
    parser.add_argument('--websocket-url', required=True)
    main(parser.parse_args())
