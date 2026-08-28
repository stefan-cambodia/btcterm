"""
Client Marionette minimal — pilote Firefox sans geckodriver ni Selenium.

Marionette est le protocole d'automatisation intégré à Firefox : on lance
le navigateur avec `--marionette`, on ouvre une socket, et on échange des
messages `longueur:[type, id, commande, paramètres]`.

Une soixantaine de lignes suffisent pour ce dont le terminal a besoin —
naviguer, exécuter du JavaScript, cliquer, glisser, capturer l'écran — ce qui
évite d'installer une pile de test navigateur complète.

Utilisé par `ui_smoke.py`.
"""
import base64, json, os, shutil, socket, subprocess, time, tempfile

class Firefox:
    def __init__(self, port=2829, size=(1920, 1080), headless=True):
        self.port = port
        self.profile = tempfile.mkdtemp(prefix="ffmar")
        with open(os.path.join(self.profile, "user.js"), "w") as f:
            f.write(f'user_pref("marionette.port", {port});\n')
            f.write('user_pref("browser.shell.checkDefaultBrowser", false);\n')
            f.write('user_pref("toolkit.telemetry.reportingpolicy.firstRun", false);\n')
            f.write('user_pref("datareporting.policy.dataSubmissionEnabled", false);\n')
            f.write('user_pref("browser.aboutwelcome.enabled", false);\n')
        cmd = ["firefox", "--marionette", "--profile", self.profile,
               "--window-size", f"{size[0]},{size[1]}", "about:blank"]
        if headless:
            cmd.insert(1, "--headless")
        self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
        self.sock = None
        self._msgid = 0
        self._connect()
        self.set_size(*size)

    def _connect(self, timeout=60):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
                s.settimeout(90)
                self.sock = s
                self._recv()                      # handshake
                self.send("WebDriver:NewSession", {})
                return
            except (ConnectionRefusedError, OSError):
                time.sleep(0.5)
        raise RuntimeError("Marionette injoignable")

    def _recv(self):
        length = b""
        while not length.endswith(b":"):
            chunk = self.sock.recv(1)
            if not chunk:
                raise RuntimeError("connexion fermée")
            length += chunk
        n = int(length[:-1])
        data = b""
        while len(data) < n:
            data += self.sock.recv(n - len(data))
        return json.loads(data)

    def send(self, name, params):
        self._msgid += 1
        payload = json.dumps([0, self._msgid, name, params]).encode()
        self.sock.sendall(str(len(payload)).encode() + b":" + payload)
        msg = self._recv()
        if msg[2]:
            raise RuntimeError(f"{name} -> {msg[2]}")
        return msg[3]

    def set_size(self, width, height):
        """Impose la taille de fenêtre.

        `--window-size` est ignoré en mode headless : sans cet appel, la
        fenêtre garde la taille par défaut de Firefox et les contrôles de
        géométrie de `ui_smoke.py` mesurent autre chose que ce qu'on voit.
        """
        return self.send("WebDriver:SetWindowRect",
                         {"width": width, "height": height, "x": 0, "y": 0})

    def get(self, url):
        return self.send("WebDriver:Navigate", {"url": url})

    def js(self, script, args=None):
        return self.send("WebDriver:ExecuteScript",
                         {"script": script, "args": args or []})["value"]

    def screenshot(self, path, full=False):
        data = self.send("WebDriver:TakeScreenshot",
                         {"full": full, "hash": False})["value"]
        with open(path, "wb") as f:
            f.write(base64.b64decode(data))
        return path

    # ── Gestes de souris ────────────────────────────────────
    #
    # Des événements de confiance, produits par le navigateur lui-même
    # (WebDriver:PerformActions), pas des MouseEvent dispatchés depuis
    # JavaScript : Lightweight Charts reconnaît ses gestes à partir de
    # mousedown/mouseup écoutés sur <html>, et un événement synthétisé sur
    # `document` ne redescend jamais jusque-là. Coordonnées en pixels de
    # la fenêtre d'affichage (viewport).

    SHIFT = "\ue008"

    def _actions(self, *sources):
        self.send("WebDriver:PerformActions", {"actions": list(sources)})
        self.send("WebDriver:ReleaseActions", {})

    def shift_drag(self, x0, y0, x1, y1):
        """Maj enfoncée pendant tout le glisser de (x0, y0) à (x1, y1)."""
        pause = {"type": "pause", "duration": 0}
        self._actions(
            {"type": "key", "id": "kb", "actions": [
                {"type": "keyDown", "value": self.SHIFT},
                pause, pause, pause, pause,
                {"type": "keyUp", "value": self.SHIFT}]},
            {"type": "pointer", "id": "mouse",
             "parameters": {"pointerType": "mouse"}, "actions": [
                pause,
                {"type": "pointerMove", "x": x0, "y": y0, "origin": "viewport"},
                {"type": "pointerDown", "button": 0},
                {"type": "pointerMove", "x": x1, "y": y1, "origin": "viewport",
                 "duration": 100},
                {"type": "pointerUp", "button": 0},
                pause]})

    def double_click(self, x, y):
        self._actions(
            {"type": "pointer", "id": "mouse",
             "parameters": {"pointerType": "mouse"}, "actions": [
                {"type": "pointerMove", "x": x, "y": y, "origin": "viewport"},
                {"type": "pointerDown", "button": 0},
                {"type": "pointerUp", "button": 0},
                {"type": "pointerDown", "button": 0},
                {"type": "pointerUp", "button": 0}]})

    def wait_for(self, expression, timeout=45, interval=0.4):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.js(f"return ({expression});"):
                return True
            time.sleep(interval)
        return False

    def close(self):
        try:
            self.send("Marionette:Quit", {})
        except Exception:
            pass
        try:
            self.proc.terminate(); self.proc.wait(timeout=10)
        except Exception:
            self.proc.kill()
        shutil.rmtree(self.profile, ignore_errors=True)
