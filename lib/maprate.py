"""Share the RainViewer tile allowance between service and dashboard interpreters."""
import json
import os
import time
from pathlib import Path


def reserve(root, clock=time.time, limit=90):
    root = Path(root)
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    lock = root / 'radar-rate.lock'
    path = root / 'radar-rate.json'
    deadline = time.monotonic() + 0.5
    while True:
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
            break
        except FileExistsError:
            try:
                if clock() - lock.stat().st_mtime > 10:
                    lock.unlink()
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.01)
        except OSError:
            return False
    try:
        now = clock()
        try:
            previous = json.loads(path.read_text())
            times = [t for t in previous if isinstance(t, (int, float)) and 0 <= now-t < 60]
        except (OSError, ValueError, TypeError):
            times = []
        if len(times) >= limit:
            return False
        times.append(now)
        # Every reader holds the same lock, including other Kodi Python processes.
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(times))
        os.replace(str(temp), str(path))
        return True
    except OSError:
        return False
    finally:
        try:
            lock.unlink()
        except FileNotFoundError:
            pass
