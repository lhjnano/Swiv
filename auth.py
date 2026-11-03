# swiv login throttling: stop guessing before it reaches PAM, so a phone-side attack
# can neither brute force the password nor push the account into a PAM lockout.
import collections
import os
import re
import threading
import time


class Limiter:
    """Per-IP and global failure limits with timed lockouts (in memory)."""

    def __init__(self, per_ip=5, global_max=20, window_sec=900, lockout_sec=600, clock=time.time):
        self.per_ip, self.global_max = per_ip, global_max
        self.window, self.lockout, self.clock = window_sec, lockout_sec, clock
        self.fails = collections.defaultdict(collections.deque)  # ip -> failure times
        self.all_fails = collections.deque()
        self.locked = {}  # ip -> unlock time; "*" locks everyone
        self.lock = threading.Lock()
        self.attempt = threading.Lock()  # one password check at a time
        self.noted = {}  # ip -> until when its "blocked" audit line is suppressed

    def _trim(self, q, now):
        while q and q[0] <= now - self.window:
            q.popleft()

    def blocked(self, ip):
        """Seconds until this ip may try again (0 = allowed)."""
        now = self.clock()
        with self.lock:
            until = max(self.locked.get(ip, 0), self.locked.get("*", 0))
            return max(0, int(until - now + 0.999))

    def note_blocked(self, ip):
        """True once per lockout period and ip, so a flood stays one audit line each."""
        now = self.clock()
        with self.lock:
            if len(self.noted) > 10000:  # keep the table bounded under wide floods
                self.noted = {k: t for k, t in self.noted.items() if t > now}
            if now < self.noted.get(ip, 0):
                return False
            self.noted[ip] = now + self.lockout
            return True

    def failed(self, ip):
        """Record a failure; returns tries left for this ip before lockout."""
        now = self.clock()
        with self.lock:
            q = self.fails[ip]
            q.append(now)
            self.all_fails.append(now)
            self._trim(q, now)
            self._trim(self.all_fails, now)
            if len(self.all_fails) >= self.global_max:
                self.locked["*"] = now + self.lockout
                self.all_fails.clear()
            if len(q) >= self.per_ip:
                self.locked[ip] = now + self.lockout
                q.clear()
                return 0
            return self.per_ip - len(q)

    def succeeded(self, ip):
        with self.lock:
            self.fails.pop(ip, None)
            self.locked.pop(ip, None)


def pam_lockout_modules(service, pam_dir):
    """Lockout modules (faillock/tally) reachable from a PAM service, one include level deep."""
    found, seen, todo = set(), set(), [service]
    while todo:
        name = todo.pop()
        if name in seen or len(seen) > 20:
            continue
        seen.add(name)
        try:
            text = open(os.path.join(pam_dir, name)).read()
        except OSError:
            continue
        found.update(re.findall(r"\b(pam_faillock|pam_tally2?)\.so", text))
        todo += re.findall(r"^\s*(?:@include|\S+\s+(?:include|substack))\s+(\S+)", text, re.M)
    return sorted(found)
