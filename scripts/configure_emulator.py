"""Apply saved emulator configuration after its HTTP server is ready."""
import argparse
import json
from pathlib import Path
import time
import urllib.error
import urllib.request


def configure(url, config_path, timeout=90):
    config = json.loads(Path(config_path).read_text(encoding='utf-8'))
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            request = urllib.request.Request(url.rstrip('/') + '/api/config',
                data=json.dumps(config).encode(), headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(request, timeout=5) as response:
                response.read()
            print(f'Emulator configured: {len(config["units"])} units → '
                  f'{config["targetHost"]}:{config["targetPort"]}', flush=True)
            return
        except urllib.error.HTTPError as exc:
            if 400 <= exc.code < 500:
                raise
            last_error = exc
        except OSError as exc:
            last_error = exc
        time.sleep(1)
    raise TimeoutError(f'Emulator unavailable after {timeout}s: {last_error}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    configure(args.url, args.config)


if __name__ == '__main__':
    main()
