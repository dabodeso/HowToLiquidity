"""Motor del juego de liquidez (sin dependencias de Streamlit)."""
from __future__ import annotations

import random
import secrets
import string
import threading
import time
from dataclasses import dataclass, field

START_CASH = 5_000_000
LAST_DAY = 16  # día de liquidación: se devuelve todo y termina el juego
SPREAD = 0.0020  # financiarse cuesta 20 pb más que lo que paga el banco por invertir
BASIS = 360
START_RATE = 3.0  # % anual de partida
MAX_RATE_MOVE = 1.0  # los tipos nunca se alejan más de 100 pb del inicial
STEP = 500_000  # paso de los deslizadores
MAX_FINANCE = 4_500_000  # deuda total máxima por jugador
MAX_PLAYERS = 80
BLOCKED_SECONDS = 10  # duración de los días con pago obligatorio (no se opera)
ALL_DONE_SECONDS = 3  # cuenta atrás cuando todos han decidido

SCHEDULED = {
    3: ("Pago a proveedor", -1_000_000),
    8: ("Alquiler", -4_000_000),
    13: ("Nóminas", -2_000_000),
    15: ("Cobro de clientes", 5_000_000),
}

# (texto, signo, mínimo en k€, máximo en k€); nunca supera 100 k€
RANDOM_EVENTS = [
    ("Hacienda te reclama una liquidación complementaria", -1, 40, 100),
    ("Renovación anual de la suscripción de Adobe", -1, 10, 30),
    ("Factura de electricidad por encima de lo previsto", -1, 15, 50),
    ("Reparación urgente del servidor", -1, 20, 80),
    ("Un cliente devuelve un pedido", -1, 30, 100),
    ("Multa por retraso en una declaración", -1, 10, 40),
    ("Pagas antes de lo previsto a un proveedor", -1, 20, 100),
    ("Viaje comercial y comida de empresa", -1, 5, 25),
    ("Renovación del seguro de la flota", -1, 30, 90),
    ("Hacienda te devuelve IVA", 1, 30, 100),
    ("Un cliente paga antes del vencimiento", 1, 40, 100),
    ("Cobras una subvención de formación", 1, 20, 60),
    ("Vendes mobiliario de oficina sobrante", 1, 10, 40),
    ("Te devuelven la fianza de un local antiguo", 1, 20, 70),
    ("La aseguradora indemniza un siniestro", 1, 15, 60),
]


def fmt_eur(x: float, decimals: int = 0) -> str:
    s = f"{x:,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{s} €"


def fmt_signed(x: float, decimals: int = 0) -> str:
    return ("+" if x >= 0 else "−") + fmt_eur(abs(x), decimals)


def fmt_pct(x: float) -> str:
    return f"{x * 100:.2f}".replace(".", ",") + " %"


@dataclass
class Player:
    pid: str
    name: str
    cash: float = float(START_CASH)
    status: str = "alive"
    bankrupt_day: int | None = None
    bankrupt_reason: str = ""
    investments: list = field(default_factory=list)
    debts: list = field(default_factory=list)
    actions: dict = field(default_factory=dict)
    log: list = field(default_factory=list)
    interest_earned: float = 0.0
    interest_paid: float = 0.0


class Game:
    def __init__(self, code: str):
        self.code = code
        self.host_token = secrets.token_urlsafe(16)
        self.seed = secrets.token_hex(8)
        self.created = time.time()
        self.lock = threading.RLock()
        self.phase = "lobby"  # lobby | day | finished
        self.day = 0
        self.day_seconds = 30
        self.day_total = 30
        self.deadline = 0.0
        self.players: dict[str, Player] = {}
        self.recaps: dict[int, dict] = {}
        self.market = self._build_market()

    # ---------- configuración determinista (misma para todos) ----------
    def _build_market(self) -> dict[int, float]:
        rng = random.Random(f"{self.seed}-mkt")
        rate = START_RATE
        out = {}
        for d in range(1, LAST_DAY + 1):
            rate += rng.choice([-0.20, -0.10, -0.05, 0.0, 0.05, 0.10, 0.20])
            rate = min(START_RATE + MAX_RATE_MOVE, max(START_RATE - MAX_RATE_MOVE, rate))
            out[d] = round(rate / 100, 4)
        return out

    def _random_event(self, d: int):
        if d >= LAST_DAY:
            return None
        rng = random.Random(f"{self.seed}-ev-{d}")
        if rng.random() >= 0.5:
            return None
        text, sign, lo, hi = rng.choice(RANDOM_EVENTS)
        return (text, sign * rng.randint(lo, hi) * 1000)

    @staticmethod
    def is_blocked(d: int) -> bool:
        return SCHEDULED.get(d, ("", 0))[1] < 0

    def _return_day(self, pid: str, d: int) -> int:
        return random.Random(f"{self.seed}-ret-{pid}-{d}").randint(d + 1, LAST_DAY)

    # ---------- jugadores ----------
    def add_player(self, name: str):
        name = " ".join(name.split())[:20]
        with self.lock:
            if self.phase != "lobby":
                return None, "La partida ya ha empezado."
            if not name:
                return None, "Escribe un nombre."
            if len(self.players) >= MAX_PLAYERS:
                return None, "Sala llena."
            if any(p.name.lower() == name.lower() for p in self.players.values()):
                return None, "Ese nombre ya está cogido."
            pid = secrets.token_urlsafe(8)
            self.players[pid] = Player(pid, name)
            return pid, ""

    def start(self, day_seconds: int) -> bool:
        with self.lock:
            if self.phase != "lobby" or not self.players:
                return False
            self.day_seconds = int(day_seconds)
            self._enter_day(1)
            return True

    # ---------- acciones ----------
    def act(self, pid: str, kind: str, amount: int = 0):
        with self.lock:
            p = self.players.get(pid)
            d = self.day
            if self.phase != "day" or p is None or p.status != "alive":
                return False, "No puedes operar ahora."
            if self.is_blocked(d):
                return False, "Hoy hay un pago obligatorio: no se puede operar."
            if d in p.actions:
                return False, "Ya has decidido hoy."
            market = self.market[d]
            if kind == "skip":
                p.actions[d] = {"kind": "skip"}
            elif kind == "invest":
                if amount <= 0 or amount % STEP or amount > p.cash:
                    return False, "No tienes caja suficiente."
                end = self._return_day(pid, d)
                p.cash -= amount
                p.investments.append({"amount": amount, "rate": market, "start": d, "end": end})
                p.actions[d] = {"kind": "invest", "amount": amount, "rate": market, "end": end}
                p.log.append((d, f"Inviertes {fmt_eur(amount)} al {fmt_pct(market)}; te lo devuelven el día {end}."))
            elif kind == "finance":
                if amount <= 0 or amount % STEP:
                    return False, "Importe no válido."
                if sum(x["amount"] for x in p.debts) + amount > MAX_FINANCE:
                    return False, f"Tu deuda total no puede superar {fmt_eur(MAX_FINANCE)}."
                rate = round(market + SPREAD, 4)
                p.cash += amount
                p.debts.append({"amount": amount, "rate": rate, "start": d})
                p.actions[d] = {"kind": "finance", "amount": amount, "rate": rate}
                p.log.append((d, f"Te financias {fmt_eur(amount)} al {fmt_pct(rate)}; se repaga el día {LAST_DAY}."))
            else:
                return False, "Acción desconocida."
            if self._all_acted():
                self.deadline = min(self.deadline, time.time() + ALL_DONE_SECONDS)
            return True, ""

    def _alive(self):
        return [p for p in self.players.values() if p.status == "alive"]

    def _all_acted(self) -> bool:
        return all(self.day in p.actions for p in self._alive())

    # ---------- avance del tiempo ----------
    def tick(self):
        with self.lock:
            if self.phase == "day" and time.time() >= self.deadline:
                self._enter_day(self.day + 1)

    def advance_now(self):
        with self.lock:
            if self.phase == "day":
                self._enter_day(self.day + 1)

    def _enter_day(self, d: int):
        sched = SCHEDULED.get(d)
        rnd = self._random_event(d)
        recap = {"day": d, "scheduled": sched, "random": rnd, "bankrupt": []}
        self.recaps[d] = recap
        for p in self._alive():
            self._apply_day(p, d, sched, rnd, recap)
        self.day = d
        if d < LAST_DAY:
            self.phase = "day"
            self.day_total = BLOCKED_SECONDS if self.is_blocked(d) else self.day_seconds
            if not self._alive():
                self.day_total = ALL_DONE_SECONDS
            self.deadline = time.time() + self.day_total
        else:
            self.phase = "finished"

    def _apply_day(self, p: Player, d: int, sched, rnd, recap):
        keep = []
        for inv in p.investments:
            if inv["end"] == d:
                interest = round(inv["amount"] * inv["rate"] * (d - inv["start"]) / BASIS, 2)
                p.cash += inv["amount"] + interest
                p.interest_earned += interest
                p.log.append((d, f"El banco te devuelve {fmt_eur(inv['amount'])} + {fmt_eur(interest, 2)} de intereses."))
            else:
                keep.append(inv)
        p.investments = keep
        if sched:
            p.cash += sched[1]
            p.log.append((d, f"{sched[0]}: {fmt_signed(sched[1])}"))
        if rnd:
            p.cash += rnd[1]
            p.log.append((d, f"{rnd[0]}: {fmt_signed(rnd[1])}"))
        if d == LAST_DAY:
            for debt in p.debts:
                interest = round(debt["amount"] * debt["rate"] * (d - debt["start"]) / BASIS, 2)
                p.cash -= debt["amount"] + interest
                p.interest_paid += interest
                p.log.append((d, f"Repagas {fmt_eur(debt['amount'])} + {fmt_eur(interest, 2)} de intereses."))
            p.debts = []
        if p.cash < 0:
            p.status = "bankrupt"
            p.bankrupt_day = d
            reason = f"Te quedaste en descubierto ({fmt_eur(p.cash)}) el día {d}."
            pending = sum(i["amount"] for i in p.investments)
            if pending:
                reason += f" Tenías {fmt_eur(pending)} invertidos que el banco aún no te había devuelto."
            p.bankrupt_reason = reason
            recap["bankrupt"].append(p.name)

    # ---------- vistas ----------
    def view_key(self, pid: str):
        with self.lock:
            p = self.players.get(pid)
            return None if p is None else (self.phase, self.day, p.status)

    def snapshot(self) -> dict:
        with self.lock:
            now = time.time()
            alive = self._alive()
            d = self.day
            bankrupt = sorted(
                (p for p in self.players.values() if p.status == "bankrupt"),
                key=lambda p: p.bankrupt_day or 0,
            )
            ranking = []
            if self.phase == "finished":
                ranking = [
                    {"name": p.name, "cash": p.cash, "net_fin": p.interest_earned - p.interest_paid}
                    for p in sorted(alive, key=lambda p: -p.cash)
                ]
            return {
                "code": self.code,
                "phase": self.phase,
                "day": d,
                "remaining": max(0.0, self.deadline - now) if self.phase == "day" else 0.0,
                "total": self.day_total,
                "players": [p.name for p in self.players.values()],
                "n_alive": len(alive),
                "acted": sum(1 for p in alive if d in p.actions),
                "bankrupt": [{"name": p.name, "day": p.bankrupt_day} for p in bankrupt],
                "recap": self.recaps.get(d),
                "blocked": self.is_blocked(d),
                "market": self.market.get(d),
                "ranking": ranking,
            }

    def player_snapshot(self, pid: str):
        with self.lock:
            p = self.players.get(pid)
            if p is None:
                return None
            d = self.day
            offer = None
            if self.phase == "day" and p.status == "alive" and not self.is_blocked(d) and d not in p.actions:
                m = self.market[d]
                r = self._return_day(pid, d)
                offer = {
                    "invest_rate": m,
                    "finance_rate": round(m + SPREAD, 4),
                    "return_day": r,
                    "change_bp": round((m - self.market[d - 1]) * 10_000) if d > 1 else 0,
                }
            rank = None
            if self.phase == "finished" and p.status == "alive":
                order = sorted(self._alive(), key=lambda q: -q.cash)
                rank = order.index(p) + 1
            return {
                "name": p.name,
                "status": p.status,
                "bankrupt_day": p.bankrupt_day,
                "bankrupt_reason": p.bankrupt_reason,
                "cash": p.cash,
                "invested": sum(i["amount"] for i in p.investments),
                "debt": sum(x["amount"] for x in p.debts),
                "phase": self.phase,
                "day": d,
                "blocked": self.is_blocked(d),
                "offer": offer,
                "action_today": p.actions.get(d),
                "log": list(p.log),
                "recap": self.recaps.get(d),
                "rank": rank,
                "n_alive": len(self._alive()),
                "net_fin": p.interest_earned - p.interest_paid,
            }


class Registry:
    def __init__(self):
        self.games: dict[str, Game] = {}
        self.lock = threading.Lock()

    def create(self) -> Game:
        with self.lock:
            cutoff = time.time() - 12 * 3600
            self.games = {c: g for c, g in self.games.items() if g.created > cutoff}
            while True:
                code = "".join(secrets.choice(string.ascii_uppercase.replace("O", "").replace("I", "")) for _ in range(4))
                if code not in self.games:
                    break
            game = Game(code)
            self.games[code] = game
            return game

    def get(self, code):
        return self.games.get((code or "").strip().upper())
