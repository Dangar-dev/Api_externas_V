#!/usr/bin/env python3
"""
Projeto 11 - Rastreador de Voos (OpenSky Network)

Desafio 1: mapa de aeronaves sobre o Brasil com filtro por categoria
    python projeto11_opensky.py mapa --categorias helicoptero comercial

Desafio 2: trajetória de um ICAO24
    python projeto11_opensky.py trajetoria --icao24 e48b3d
    python projeto11_opensky.py trajetoria --icao24 e48b3d --poll 10 --intervalo 30   (alternativa)

Sem login funciona com créditos diários baixos. Para mais limite, defina
OPENSKY_CLIENT_ID e OPENSKY_CLIENT_SECRET (OAuth2, criado na conta OpenSky).
O endpoint /tracks é experimental e pode exigir autenticação; por isso há o
modo --poll, que monta a trajetória coletando posições ao vivo.
"""
import argparse
import os
import time

import folium
import pandas as pd
import plotly.express as px
import requests

API = "https://opensky-network.org/api"
TOKEN_URL = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"
BRASIL = dict(lamin=-34.0, lomin=-74.0, lamax=5.5, lomax=-34.0)
COLS = ["icao24", "callsign", "pais_origem", "t_posicao", "ultimo_contato", "lon", "lat", "alt_baro",
        "no_solo", "velocidade_ms", "rumo", "vrate", "sensores", "alt_geo", "squawk", "spi",
        "fonte_pos", "categoria"]
GRUPOS = {  # nome -> códigos de categoria ADS-B
    "helicoptero": {8}, "comercial": {4, 5, 6}, "pequeno": {2, 3, 7},
    "planador_leve": {9, 10, 12}, "drone": {14}, "sem_info": {0, 1},
}
CORES = {"helicoptero": "red", "comercial": "blue", "pequeno": "green", "planador_leve": "purple",
         "drone": "orange", "sem_info": "gray", "outro": "black"}


def headers():
    cid, sec = os.getenv("OPENSKY_CLIENT_ID"), os.getenv("OPENSKY_CLIENT_SECRET")
    if not (cid and sec):
        return {}
    r = requests.post(TOKEN_URL, data={"grant_type": "client_credentials", "client_id": cid,
                                       "client_secret": sec}, timeout=30)
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def chamar(path, params, hdr):
    r = requests.get(f"{API}{path}", params=params, headers=hdr, timeout=40)
    if r.status_code == 429:
        espera = r.headers.get("X-Rate-Limit-Retry-After-Seconds", "?")
        raise SystemExit(f"Limite de créditos atingido. Tente de novo em {espera}s ou use OAuth.")
    if r.status_code in (401, 403):
        raise SystemExit(f"{path} exige autenticação (HTTP {r.status_code}). Defina OPENSKY_CLIENT_ID/SECRET.")
    r.raise_for_status()
    return r.json()


def estados(hdr, **filtros):
    js = chamar("/states/all", {"extended": 1, **filtros}, hdr)
    linhas = [(s + [None] * 18)[:18] for s in (js.get("states") or [])]
    df = pd.DataFrame(linhas, columns=COLS)
    df["callsign"] = df["callsign"].str.strip()
    df["grupo"] = df["categoria"].apply(
        lambda c: next((g for g, cods in GRUPOS.items() if c in cods), "outro"))
    return df.dropna(subset=["lat", "lon"])


# ------------------------------------------------------------ desafio 1
def mapa(categorias, saida):
    df = estados(headers(), **BRASIL)
    print(f"{len(df)} aeronaves com posição sobre o Brasil.")
    if categorias:
        df = df[df["grupo"].isin(categorias)]
        print(f"{len(df)} após filtro {categorias}")
    # OpenStreetMap não exige chave; CartoDB passou a exigir autenticação para tiles.
    m = folium.Map(location=[-14, -52], zoom_start=4, tiles="OpenStreetMap")
    camadas = {g: folium.FeatureGroup(name=f"{g} ({(df['grupo'] == g).sum()})") for g in df["grupo"].unique()}
    for _, a in df.iterrows():
        alt = f"{a.alt_geo:.0f} m" if pd.notna(a.alt_geo) else "n/d"
        vel = f"{a.velocidade_ms * 3.6:.0f} km/h" if pd.notna(a.velocidade_ms) else "n/d"
        popup = (f"<b>{a.callsign or 'sem callsign'}</b><br>ICAO24: {a.icao24}<br>Grupo: {a.grupo}<br>"
                 f"Altitude: {alt}<br>Velocidade: {vel}<br>{'No solo' if a.no_solo else 'Em voo'}")
        folium.CircleMarker([a.lat, a.lon], radius=4, color=CORES.get(a.grupo, "black"), fill=True,
                            fill_opacity=0.8, popup=folium.Popup(popup, max_width=250)).add_to(camadas[a.grupo])
    for c in camadas.values():
        c.add_to(m)
    folium.LayerControl(collapsed=False).add_to(m)
    m.save(saida)
    print(f"Mapa salvo em {saida}")
    print(df["grupo"].value_counts().to_string())


# ------------------------------------------------------------ desafio 2
def trajetoria_tracks(icao24, hdr):
    # A API expõe as trajetórias no recurso /tracks/all.
    js = chamar("/tracks/all", {"icao24": icao24.lower(), "time": 0}, hdr)
    cols = ["t", "lat", "lon", "alt_baro", "rumo", "no_solo"]
    df = pd.DataFrame(js.get("path") or [], columns=cols)
    return df, js.get("callsign")


def trajetoria_poll(icao24, hdr, n, intervalo):
    pts = []
    for i in range(n):
        df = estados(hdr, icao24=icao24.lower())
        if not df.empty:
            a = df.iloc[0]
            pts.append({"t": int(time.time()), "lat": a.lat, "lon": a.lon, "alt_baro": a.alt_baro})
            print(f"[{i + 1}/{n}] {a.lat:.3f}, {a.lon:.3f}")
        else:
            print(f"[{i + 1}/{n}] aeronave não encontrada agora")
        if i < n - 1:
            time.sleep(intervalo)
    return pd.DataFrame(pts), None


def trajetoria(icao24, poll, intervalo):
    hdr = headers()
    if poll:
        df, callsign = trajetoria_poll(icao24, hdr, poll, intervalo)
    else:
        df, callsign = trajetoria_tracks(icao24, hdr)
    df = df.dropna(subset=["lat", "lon"])
    if df.empty:
        raise SystemExit("Sem pontos. A aeronave pode estar em solo/offline; tente --poll.")
    m = folium.Map(location=[df.lat.mean(), df.lon.mean()], zoom_start=6, tiles="OpenStreetMap")
    folium.PolyLine(df[["lat", "lon"]].values.tolist(), color="blue", weight=3).add_to(m)
    folium.Marker(df[["lat", "lon"]].iloc[0].tolist(), tooltip="Início",
                  icon=folium.Icon(color="green")).add_to(m)
    folium.Marker(df[["lat", "lon"]].iloc[-1].tolist(), tooltip=f"Fim ({callsign or icao24})",
                  icon=folium.Icon(color="red")).add_to(m)
    m.fit_bounds(df[["lat", "lon"]].values.tolist())
    m.save(f"trajetoria_{icao24}.html")
    df["hora"] = pd.to_datetime(df["t"], unit="s")
    px.line(df, x="hora", y="alt_baro", title=f"Altitude barométrica - {icao24}",
            labels={"alt_baro": "m"}).write_html(f"altitude_{icao24}.html")
    print(f"{len(df)} pontos. Salvos trajetoria_{icao24}.html e altitude_{icao24}.html")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("mapa")
    a.add_argument("--categorias", nargs="*", choices=list(GRUPOS) + ["outro"], help="vazio = todas")
    a.add_argument("--saida", default="mapa_brasil.html")
    b = sub.add_parser("trajetoria")
    b.add_argument("--icao24", required=True)
    b.add_argument("--poll", type=int, default=0, help="nº de coletas ao vivo (alternativa ao /tracks)")
    b.add_argument("--intervalo", type=int, default=30)
    x = ap.parse_args()
    mapa(x.categorias, x.saida) if x.cmd == "mapa" else trajetoria(x.icao24, x.poll, x.intervalo)
