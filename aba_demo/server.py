"""Local-only demonstration server. Run with python -m aba_demo.server."""
import json
import secrets
import threading
import math
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_JSON_BYTES = 1024 * 1024
MAX_CONTEXT_JSON_BYTES = 20 * 1024 * 1024


def strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def invalid_constant(value):
    raise ValueError('Non-finite JSON number')


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_json(self, status, value):
        data = json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.end_headers()
        self.wfile.write(data)

    def trusted(self, write=False):
        host = f'127.0.0.1:{self.server.server_address[1]}'
        if self.headers.get('Host') != host:
            return False
        if write:
            if self.headers.get('Origin') not in (None, 'http://' + host):
                return False
            if self.headers.get('Sec-Fetch-Site') not in (None, 'same-origin', 'none'):
                return False
            token = self.headers.get('X-ABA-Token', '')
            return token.isascii() and secrets.compare_digest(token, self.server.token)
        return True

    def do_POST(self):
        if not self.trusted(write=True):
            self.send_json(403, {'error': 'Local token and same-origin request required'})
            return
        if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            # On Windows, closing a socket with an unread request body can reset
            # the connection before the client receives the 415 response.
            try:
                rejected_size = int(self.headers.get('Content-Length', '-1'))
                body_limit = (MAX_CONTEXT_JSON_BYTES if self.path == '/api/context/validate'
                              else MAX_JSON_BYTES)
                if 0 <= rejected_size <= body_limit:
                    self.connection.settimeout(5)
                    self.rfile.read(rejected_size)
            except (ValueError, OSError, TimeoutError):
                pass
            self.send_json(415, {'error': 'Content-Type must be application/json'})
            return
        try:
            if self.headers.get('Transfer-Encoding'):
                raise ValueError('Transfer encoding is unsupported')
            size = int(self.headers.get('Content-Length', '-1'))
            body_limit = (MAX_CONTEXT_JSON_BYTES if self.path == '/api/context/validate'
                          else MAX_JSON_BYTES)
            if size > body_limit:
                self.send_json(413, {'error': 'JSON exceeds request size limit'})
                return
            if size < 0:
                raise ValueError('Content-Length required')
            self.connection.settimeout(5)
            payload = json.loads(self.rfile.read(size).decode('utf-8'),
                                 parse_constant=invalid_constant, object_pairs_hook=strict_object)
            if not isinstance(payload, dict):
                raise ValueError('JSON body must be an object')
            if 'config' in payload and not isinstance(payload['config'], dict):
                raise ValueError('config must be an object')
        except (ValueError, UnicodeError, TimeoutError, RecursionError) as exc:
            self.send_json(400, {'error': str(exc)})
            return
        if self.path == '/api/context/validate':
            try:
                duration = float(self.headers.get('X-ABA-Source-Duration', ''))
                if not math.isfinite(duration) or duration <= 0:
                    raise ValueError('X-ABA-Source-Duration must be finite positive seconds')
                from .context_schema import validate_context_document
                validate_context_document(payload, duration)
            except (ValueError, TypeError) as exc:
                self.send_json(400, {'error': str(exc)})
                return
            self.send_json(200, {'status': 'valid'})
            return
        try:
            with self.server.state_lock:
                if self.path == '/api/reset':
                    from .engine import Engine
                    self.server.engine = Engine(payload.get('config'))
                    self.server.last_analysis = None
                    result = {'status': 'reset'}
                elif self.path == '/api/activity':
                    self.engine().set_activity(payload.get('activity'))
                    result = {'activity': self.engine().activity}
                elif self.path == '/api/observe':
                    result = self.engine().update(payload)
                elif self.path == '/api/open':
                    path = payload.get('path')
                    if (not isinstance(path, str) or not path or '://' in path
                            or path.startswith(('//', '\\')) or '\x00' in path):
                        raise ValueError('Provide an absolute local video path, not a URL or network share')
                    file = Path(path)
                    if (not file.is_absolute() or file.suffix.lower() not in
                            {'.mp4', '.mov', '.mkv', '.webm', '.avi', '.m4v'} or not file.is_file()):
                        raise ValueError('Select an existing local mp4/mov/mkv/webm/avi/m4v video')
                    from .vision import VideoAnalyzer
                    analyzer = VideoAnalyzer(str(file), payload.get('config', {}))
                    try:
                        result = analyzer.preview()
                    except Exception:
                        analyzer.close()
                        raise
                    if self.server.analyzer is not None:
                        self.server.analyzer.close()
                    self.server.analyzer = analyzer
                    self.reset_engine_context()
                elif self.path in ('/api/select', '/api/analyze'):
                    if self.server.analyzer is None:
                        self.send_json(409, {'error': 'Open a local video with /api/open first'})
                        return
                    if self.path == '/api/select':
                        x, y = payload.get('x'), payload.get('y')
                        if not all(type(v) in (int, float) and math.isfinite(v) and 0 <= v <= 1 for v in (x, y)):
                            raise ValueError('x and y must be normalized numbers in [0, 1]')
                        result = self.server.analyzer.select_target(x, y)
                        self.reset_engine_context()
                        if result is None:
                            result = {'status': 'selected'}
                    else:
                        time = payload.get('time')
                        if type(time) not in (int, float) or not math.isfinite(time) or time < 0:
                            raise ValueError('time must be finite nonnegative seconds')
                        observation = self.server.analyzer.analyze(time)
                        previous = self.server.last_analysis
                        if previous and previous['observation']['time'] == observation['time']:
                            result = dict(previous, status='no_new_frame')
                        else:
                            result = {'observation': observation, 'state': self.engine().update(observation)}
                            self.server.last_analysis = result
                else:
                    self.send_json(404, {'error': 'Not found'})
                    return
            self.send_json(200, result)
        except EOFError as exc:
            self.send_json(200, {'status': 'end_of_video', 'detail': str(exc)})
        except (ValueError, TypeError, KeyError) as exc:
            self.send_json(400, {'error': str(exc)})
        except (ImportError, RuntimeError, OSError) as exc:
            self.send_json(503, {'error': 'Local analysis unavailable: ' + str(exc),
                                 'action': 'Install requirements-demo-vision.txt and provision local pose weights, or import the matching Colab precomputed JSON. No inference results were generated.'})

    def reset_engine_context(self):
        engine = self.engine()
        activity = engine.activity
        engine.reset()
        engine.set_activity(activity)
        self.server.last_analysis = None

    def engine(self):
        if self.server.engine is None:
            from .engine import Engine
            self.server.engine = Engine()
        return self.server.engine

    def do_GET(self):
        if not self.trusted():
            self.send_json(403, {'error': 'Invalid host'})
            return
        if self.path == '/':
            text = (Path(__file__).parent / 'static' / 'index.html').read_text(encoding='utf-8')
            nonce = secrets.token_urlsafe(24)
            data = text.replace('__ABA_TOKEN__', self.server.token).replace('__CSP_NONCE__', nonce).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy',
                             f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; img-src 'self' blob: data:; media-src blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(data)
        elif self.path == '/health':
            self.send_json(200, {'status': 'ok'})
        else:
            self.send_json(404, {'error': 'Not found'})


class LocalServer(ThreadingHTTPServer):
    def shutdown(self):
        super().shutdown()
        self.close_analyzer()

    def server_close(self):
        super().server_close()
        self.close_analyzer()

    def close_analyzer(self):
        if not hasattr(self, 'state_lock'):
            return
        with self.state_lock:
            if self.analyzer is not None:
                self.analyzer.close()
                self.analyzer = None


def create_server(port=8766):
    server = LocalServer(('127.0.0.1', port), Handler)
    server.token = secrets.token_urlsafe(32)
    server.state_lock = threading.RLock()
    server.engine = None
    server.analyzer = None
    server.last_analysis = None
    return server


def main():
    server = create_server()
    print('ABA local demo: http://127.0.0.1:8766', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
