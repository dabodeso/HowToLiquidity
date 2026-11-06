import html
import io
import math
import socket
from functools import lru_cache

import qrcode
import streamlit as st

import engine as E

params = st.query_params
IS_HOST_URL = "h" in params
st.set_page_config(
    page_title="Liquidez: el juego",
    layout="wide" if IS_HOST_URL else "centered",
)

CSS = """
<style>
.cal {display:grid; grid-template-columns:repeat(8,1fr); gap:10px; margin:6px 0 18px;}
.cell {border:2px solid #d0d7de; border-radius:12px; padding:8px 10px; min-height:100px;
       background:#fff; color:#1f2328;}
.cell .n {font-size:1.5rem; font-weight:800;}
.cell .t {font-size:.9rem; line-height:1.1;}
.cell .a {font-size:1.2rem; font-weight:800; margin-top:4px;}
.cell.past {opacity:.45;}
.cell.today {border-color:#2563eb; box-shadow:0 0 0 5px rgba(37,99,235,.3);}
.cell.out {background:#fee2e2; border-color:#ef4444;}
.cell.out .a {color:#b91c1c;}
.cell.in {background:#dcfce7; border-color:#22c55e;}
.cell.in .a {color:#15803d;}
.cell.end {background:#fef9c3; border-color:#eab308;}
.big {font-size:4rem; font-weight:900; line-height:1;}
.code {font-size:4.5rem; font-weight:900; letter-spacing:.3rem;}
.chip {display:inline-block; background:#e0e7ff; color:#1e1b4b; border-radius:999px;
       padding:4px 14px; margin:3px; font-size:1.2rem;}
.chip.alive {background:#dcfce7; color:#14532d;}
.bk {color:#dc2626; font-size:1.5rem; font-weight:700; margin:4px 0;}
.bk s {text-decoration-thickness:3px;}
.bk small {font-weight:400; color:#991b1b;}
.kpis {display:flex; gap:8px; margin:8px 0 14px;}
.kpi {flex:1; border-radius:12px; padding:8px 10px; background:#f1f5f9; color:#0f172a; text-align:center;}
.kpi .l {font-size:.75rem; text-transform:uppercase; color:#475569;}
.kpi .v {font-size:1.05rem; font-weight:800;}
.kpi.neg .v {color:#dc2626;}
.board {width:100%; border-collapse:collapse; font-size:1.4rem;}
.board td, .board th {padding:6px 12px; border-bottom:1px solid #cbd5e1; text-align:right;}
.board td:first-child, .board th:first-child {text-align:left;}
.dead {background:#fee2e2; color:#991b1b; border-radius:12px; padding:16px; text-align:center;}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


@st.cache_resource
def get_registry() -> E.Registry:
    return E.Registry()


registry = get_registry()


# ---------------------------------------------------------------- utilidades
def esc(s: str) -> str:
    return html.escape(s)


def mm(v: float) -> str:
    return f"{v / 1e6:+.1f} M€".replace(".", ",").replace("-", "−")


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def detect_base_url() -> str:
    headers = st.context.headers
    host = headers.get("Host", "localhost:8501")
    proto = headers.get("X-Forwarded-Proto", "http")
    name, _, port = host.partition(":")
    if name in ("localhost", "127.0.0.1"):
        host = f"{lan_ip()}:{port}" if port else lan_ip()
    return f"{proto}://{host}"


@lru_cache(maxsize=16)
def qr_png(url: str) -> bytes:
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def calendar_html(s: dict) -> str:
    today = s["day"] if s["phase"] != "lobby" else 0
    cells = []
    for d in range(1, E.LAST_DAY + 1):
        cls = ["cell"]
        title, amount = "", ""
        if d in E.SCHEDULED:
            label, v = E.SCHEDULED[d]
            title, amount = esc(label), mm(v)
            cls.append("out" if v < 0 else "in")
        elif d == E.LAST_DAY:
            title, amount = "Se devuelve todo: deudas e inversiones", ""
            cls.append("end")
        if d < today:
            cls.append("past")
        elif d == today:
            cls.append("today")
        cells.append(
            f'<div class="{" ".join(cls)}"><div class="n">{d}</div>'
            f'<div class="t">{title}</div><div class="a">{amount}</div></div>'
        )
    return f'<div class="cal">{"".join(cells)}</div>'


def bankrupt_html(s: dict) -> str:
    if not s["bankrupt"]:
        return '<div style="color:#64748b">Nadie ha quebrado... todavía.</div>'
    return "".join(
        f'<div class="bk"><s>{esc(b["name"])}</s> <small>quiebra el día {b["day"]}</small></div>'
        for b in s["bankrupt"]
    )


def recap_text(recap: dict | None) -> list[str]:
    if not recap:
        return []
    lines = []
    if recap["scheduled"]:
        label, v = recap["scheduled"]
        lines.append(f"**{label}**: {E.fmt_signed(v)}")
    if recap["random"]:
        text, v = recap["random"]
        lines.append(f"Suceso del día: {text} ({E.fmt_signed(v)})")
    elif recap["day"] < E.LAST_DAY:
        lines.append("Hoy no ha ocurrido ningún suceso extraordinario.")
    return lines


# ---------------------------------------------------------------- anfitrión
def render_lobby(game: E.Game, s: dict):
    url = f"{st.session_state['base_url'].rstrip('/')}/?room={s['code']}"
    left, right = st.columns([1, 1])
    with left:
        st.image(qr_png(url), width=380)
        st.caption(url)
    with right:
        st.markdown("### Escanea el QR o entra con el código")
        st.markdown(f'<div class="code">{s["code"]}</div>', unsafe_allow_html=True)
        st.markdown(f"### Jugadores ({len(s['players'])})")
        st.markdown(
            "".join(f'<span class="chip">{esc(n)}</span>' for n in s["players"]) or "Esperando jugadores...",
            unsafe_allow_html=True,
        )
        if st.button("Empezar partida", type="primary", disabled=not s["players"]):
            game.start(st.session_state["day_seconds"])


def render_playing(game: E.Game, s: dict):
    st.markdown(calendar_html(s), unsafe_allow_html=True)
    left, right = st.columns([3, 2])
    with left:
        if s["phase"] == "day":
            st.markdown(f"## Día {s['day']} de {E.LAST_DAY}")
            st.markdown(f'<div class="big">{math.ceil(s["remaining"])} s</div>', unsafe_allow_html=True)
            st.progress(min(1.0, s["remaining"] / max(1, s["total"])))
            if s["blocked"]:
                st.warning("Día con pago obligatorio: hoy no se puede operar con el banco.")
            else:
                st.markdown(
                    f"### {s['acted']} de {s['n_alive']} jugadores han decidido\n"
                    f"Tipo de mercado hoy: invertir al {E.fmt_pct(s['market'])} · "
                    f"financiarse al {E.fmt_pct(s['market'] + E.SPREAD)}"
                )
        for line in recap_text(s["recap"]):
            st.markdown(f"#### {line}")
        if s["phase"] == "day" and st.button("Saltar al siguiente día"):
            game.advance_now()
    with right:
        st.markdown("## Quiebras")
        st.markdown(bankrupt_html(s), unsafe_allow_html=True)


def render_finished(s: dict):
    st.markdown(calendar_html(s), unsafe_allow_html=True)
    left, right = st.columns([3, 2])
    with left:
        st.markdown("## Clasificación final")
        if s["ranking"]:
            rows = "".join(
                f"<tr><td>{i}. {esc(r['name'])}</td><td>{E.fmt_eur(r['cash'])}</td>"
                f"<td>{E.fmt_signed(r['net_fin'], 2)}</td></tr>"
                for i, r in enumerate(s["ranking"], 1)
            )
            st.markdown(
                f'<table class="board"><tr><th>Jugador</th><th>Caja final</th><th>Resultado financiero</th></tr>{rows}</table>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown("Nadie ha sobrevivido.")
        st.markdown(
            "**Qué hemos aprendido:** el dinero entra y sale en días distintos. Financiarse a corto plazo "
            "cubre los descubiertos (y cuesta poco); invertir la liquidez sobrante la rentabiliza, pero te la "
            "bloquea hasta que el banco te la devuelve."
        )
    with right:
        st.markdown("## Quiebras")
        st.markdown(bankrupt_html(s), unsafe_allow_html=True)
    if st.button("Nueva partida"):
        st.query_params.clear()
        st.rerun()


@st.fragment(run_every=1)
def host_screen(game: E.Game):
    game.tick()
    s = game.snapshot()
    if s["phase"] == "lobby":
        render_lobby(game, s)
    elif s["phase"] == "day":
        render_playing(game, s)
    else:
        render_finished(s)


def host_view(game: E.Game):
    if "base_url" not in st.session_state:
        st.session_state["base_url"] = detect_base_url()
    with st.sidebar:
        st.text_input("URL pública de la app (para el QR)", key="base_url")
        st.slider("Segundos por día", 10, 90, 30, 5, key="day_seconds")
    st.title("Liquidez: el juego")
    host_screen(game)


# ---------------------------------------------------------------- jugador
def join_form(game: E.Game):
    st.title("Liquidez: el juego")
    if game.phase != "lobby":
        st.error("La partida ya ha empezado.")
        return
    with st.form("join"):
        name = st.text_input("Tu nombre", max_chars=20)
        if st.form_submit_button("Entrar", type="primary", use_container_width=True):
            pid, err = game.add_player(name)
            if pid:
                st.query_params["p"] = pid
                st.rerun()
            else:
                st.error(err)


@st.fragment(run_every=1)
def player_header(game: E.Game, pid: str, seen_key: tuple):
    game.tick()
    if game.view_key(pid) != seen_key:
        st.rerun()
    s = game.snapshot()
    if s["phase"] == "day":
        st.markdown(f"**Día {s['day']} de {E.LAST_DAY}** · quedan {math.ceil(s['remaining'])} s")
        st.progress(min(1.0, s["remaining"] / max(1, s["total"])))


def kpis_html(me: dict) -> str:
    neg = " neg" if me["cash"] < 0 else ""
    return (
        '<div class="kpis">'
        f'<div class="kpi{neg}"><div class="l">Caja</div><div class="v">{E.fmt_eur(me["cash"])}</div></div>'
        f'<div class="kpi"><div class="l">Invertido</div><div class="v">{E.fmt_eur(me["invested"])}</div></div>'
        f'<div class="kpi"><div class="l">Deuda</div><div class="v">{E.fmt_eur(me["debt"])}</div></div>'
        "</div>"
    )


def amount_picker(label: str, max_amount: int, key: str) -> int:
    step = E.STEP
    if max_amount > step:
        m = st.slider(label, step / 1e6, max_amount / 1e6, min(1.0, max_amount / 1e6), step / 1e6, format="%.1f M€", key=key)
    else:
        m = step / 1e6
        st.caption(f"{label}: {m:.1f} M€".replace(".", ","))
    return round(m * 1_000_000)


def decision_panel(game: E.Game, pid: str, me: dict):
    o = me["offer"]
    d = me["day"]
    if d > 1:
        bp = o["change_bp"]
        trend = "sube" if bp > 0 else "baja" if bp < 0 else "se mantiene"
        st.caption(f"Los tipos hoy {trend}" + (f" {abs(bp)} pb respecto a ayer." if bp else " respecto a ayer."))
    inv_tab, fin_tab = st.tabs(["Invertir", "Financiarme"])
    with inv_tab:
        st.markdown(
            f"El banco te ofrece **{E.fmt_pct(o['invest_rate'])}** anual y te devuelve el dinero el "
            f"**día {o['return_day']}**. Mientras tanto no podrás usar ese dinero."
        )
        max_amount = int(max(me["cash"], 0) // E.STEP) * E.STEP
        if max_amount < E.STEP:
            st.warning("No tienes caja para invertir.")
        else:
            amount = amount_picker("Importe a invertir", max_amount, f"inv-amt-{d}")
            interest = round(amount * o["invest_rate"] * (o["return_day"] - d) / E.BASIS, 2)
            st.info(
                f"El día {o['return_day']} recibirías **{E.fmt_eur(amount + interest, 2)}** "
                f"({E.fmt_eur(amount)} + {E.fmt_eur(interest, 2)} de intereses)."
            )
            if st.button("Invertir", type="primary", use_container_width=True, key=f"inv-{d}"):
                ok, msg = game.act(pid, "invest", amount)
                st.rerun() if ok else st.error(msg)
    with fin_tab:
        st.markdown(
            f"Te financias al **{E.fmt_pct(o['finance_rate'])}** anual (20 pb más que lo que paga el banco por invertir) "
            f"y lo repagas el **día {E.LAST_DAY}**."
        )
        room_left = int(E.MAX_FINANCE - me["debt"])
        if room_left < E.STEP:
            st.warning(f"Has alcanzado el límite de deuda ({E.fmt_eur(E.MAX_FINANCE)}).")
        else:
            st.caption(f"Límite de deuda total: {E.fmt_eur(E.MAX_FINANCE)}.")
            amount = amount_picker("Importe a financiar", room_left, f"fin-amt-{d}")
            interest = round(amount * o["finance_rate"] * (E.LAST_DAY - d) / E.BASIS, 2)
            st.info(
                f"El día {E.LAST_DAY} devolverías **{E.fmt_eur(amount + interest, 2)}** "
                f"({E.fmt_eur(amount)} + {E.fmt_eur(interest, 2)} de intereses)."
            )
            if st.button("Financiarme", type="primary", use_container_width=True, key=f"fin-{d}"):
                ok, msg = game.act(pid, "finance", amount)
                st.rerun() if ok else st.error(msg)
    if st.button("No hacer nada hoy", use_container_width=True, key=f"skip-{d}"):
        game.act(pid, "skip")
        st.rerun()


def player_view(game: E.Game):
    pid = st.query_params.get("p")
    me = game.player_snapshot(pid) if pid else None
    if me is None:
        join_form(game)
        return
    st.markdown(f"### {esc(me['name'])}")
    player_header(game, pid, game.view_key(pid))
    st.markdown(kpis_html(me), unsafe_allow_html=True)

    if me["phase"] == "lobby":
        st.info("Estás dentro. Mira la pantalla principal: la partida empezará en breve.")
    elif me["status"] == "bankrupt":
        st.markdown(
            f'<div class="dead"><h2>Has quebrado</h2>{esc(me["bankrupt_reason"])}</div>',
            unsafe_allow_html=True,
        )
    elif me["phase"] == "finished":
        st.success(
            f"Has sobrevivido. Puesto {me['rank']} de {me['n_alive']} con {E.fmt_eur(me['cash'])} "
            f"(resultado financiero: {E.fmt_signed(me['net_fin'], 2)})."
        )
    else:
        for line in recap_text(me["recap"]):
            st.markdown(f"- {line}")
        if me["blocked"]:
            st.warning("Hoy hay un pago obligatorio: no se puede operar con el banco.")
        elif me["action_today"]:
            a = me["action_today"]
            if a["kind"] == "skip":
                st.info("Hoy no haces nada. Espera al siguiente día.")
            elif a["kind"] == "invest":
                st.info(f"Has invertido {E.fmt_eur(a['amount'])}; te lo devuelven el día {a['end']}.")
            else:
                st.info(f"Te has financiado {E.fmt_eur(a['amount'])}; lo repagas el día {E.LAST_DAY}.")
        else:
            decision_panel(game, pid, me)

    if me["log"]:
        with st.expander("Mi diario"):
            for day, text in reversed(me["log"]):
                st.markdown(f"**Día {day}** · {text}")


# ---------------------------------------------------------------- inicio
def landing():
    st.title("Liquidez: el juego")
    st.markdown("Simulación de 15 días para entender por qué una empresa necesita financiación a corto plazo o invertir su liquidez.")
    left, right = st.columns(2)
    with left:
        st.subheader("Soy el anfitrión")
        if st.button("Crear partida", type="primary"):
            game = registry.create()
            st.query_params.update({"room": game.code, "h": game.host_token})
            st.rerun()
    with right:
        st.subheader("Soy jugador")
        with st.form("code"):
            code = st.text_input("Código de sala", max_chars=4)
            if st.form_submit_button("Continuar"):
                if registry.get(code):
                    st.query_params["room"] = code.strip().upper()
                    st.rerun()
                else:
                    st.error("Sala no encontrada.")


room = registry.get(params.get("room"))
if room is None:
    if params.get("room"):
        st.error("Sala no encontrada o caducada.")
    landing()
elif params.get("h") == room.host_token:
    host_view(room)
else:
    player_view(room)
