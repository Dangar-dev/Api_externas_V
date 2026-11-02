#!/usr/bin/env python3
"""
Projeto 12 - Dashboard de Exploração Espacial (NASA Open APIs)

    export NASA_API_KEY=sua_chave      # grátis em https://api.nasa.gov (sem ela usa DEMO_KEY, limite baixo)
    python projeto12_nasa.py --inicio 2025-01-01 --fim 2025-01-14

Gera dashboard_nasa.html com 4 abas:
  APOD | Exoplanetas por método | Eventos naturais (48h) | Asteroides (distância x velocidade)
"""
import argparse
import html
import os
from datetime import date, datetime, timedelta

import pandas as pd
import plotly.express as px
import requests

KEY = os.getenv("NASA_API_KEY", "DEMO_KEY")


def get(url, params=None):
    r = requests.get(url, params=params, timeout=60)
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------- asteroides (NeoWs)
def asteroides(inicio, fim):
    """O feed aceita no máximo 7 dias por chamada: dividimos o intervalo em blocos."""
    linhas, d = [], inicio
    while d <= fim:
        bloco_fim = min(d + timedelta(days=6), fim)
        js = get("https://api.nasa.gov/neo/rest/v1/feed",
                 {"start_date": d, "end_date": bloco_fim, "api_key": KEY})
        for dia, objs in js["near_earth_objects"].items():
            for o in objs:
                dm = o["estimated_diameter"]["meters"]
                for ap in o["close_approach_data"]:
                    linhas.append({
                        "nome": o["name"], "data": dia,
                        "diametro_m": (dm["estimated_diameter_min"] + dm["estimated_diameter_max"]) / 2,
                        "perigoso": o["is_potentially_hazardous_asteroid"],
                        "velocidade_kms": float(ap["relative_velocity"]["kilometers_per_second"]),
                        "distancia_km": float(ap["miss_distance"]["kilometers"]),
                        "distancia_lunar": float(ap["miss_distance"]["lunar"]),
                    })
        d = bloco_fim + timedelta(days=1)
    return pd.DataFrame(linhas).drop_duplicates(["nome", "data"])


def fig_asteroides(inicio, fim):
    df = asteroides(inicio, fim)
    fig = px.scatter(df, x="distancia_lunar", y="velocidade_kms", size="diametro_m", color="perigoso",
                     hover_name="nome", log_x=True, size_max=40,
                     color_discrete_map={True: "crimson", False: "steelblue"},
                     title=f"Asteroides próximos: distância x velocidade ({inicio} a {fim}) - {len(df)} objetos",
                     labels={"distancia_lunar": "Distância da Terra (distâncias lunares, escala log)",
                             "velocidade_kms": "Velocidade relativa (km/s)", "perigoso": "Potencialmente perigoso"})
    return fig


# ---------------------------------------------------------------- APOD
def aba_apod():
    js = get("https://api.nasa.gov/planetary/apod", {"api_key": KEY})
    if js.get("media_type") == "video":
        midia = f'<iframe width="800" height="450" src="{html.escape(js["url"])}" allowfullscreen></iframe>'
    else:
        midia = f'<img src="{html.escape(js.get("hdurl") or js["url"])}" style="max-width:100%;max-height:600px">'
    return (f"<h2>{html.escape(js['title'])}</h2><p><i>{js['date']}"
            f"{' - © ' + html.escape(js['copyright'].strip()) if js.get('copyright') else ''}</i></p>"
            f"{midia}<p style='max-width:900px'>{html.escape(js['explanation'])}</p>")


# ---------------------------------------------------------------- exoplanetas
def fig_exoplanetas():
    q = ("select discoverymethod, count(*) as n from ps where default_flag=1 "
         "group by discoverymethod order by n desc")
    js = get("https://exoplanetarchive.ipac.caltech.edu/TAP/sync", {"query": q, "format": "json"})
    df = pd.DataFrame(js)
    return px.bar(df, x="n", y="discoverymethod", orientation="h", text="n", log_x=True,
                  title="Exoplanetas confirmados por método de descoberta (escala log)",
                  labels={"n": "Nº de planetas", "discoverymethod": ""}).update_yaxes(autorange="reversed")


# ---------------------------------------------------------------- EONET
def fig_eventos():
    js = get("https://eonet.gsfc.nasa.gov/api/v3/events", {"days": 2, "status": "all"})
    pts = []
    for e in js["events"]:
        g = e["geometry"][-1]
        c = g["coordinates"]
        if g["type"] == "Polygon":
            ring = c[0]
            lon = sum(p[0] for p in ring) / len(ring)
            lat = sum(p[1] for p in ring) / len(ring)
        else:
            lon, lat = c[0], c[1]
        pts.append({"evento": e["title"], "categoria": e["categories"][0]["title"],
                    "data": g["date"][:16], "lat": lat, "lon": lon})
    df = pd.DataFrame(pts)
    if df.empty:
        return None
    return px.scatter_geo(df, lat="lat", lon="lon", color="categoria", hover_name="evento",
                          hover_data={"data": True, "lat": False, "lon": False},
                          projection="natural earth", title=f"Eventos naturais (EONET) - últimas 48h: {len(df)}")


# ---------------------------------------------------------------- montagem
def seguro(fn, *a):
    try:
        return fn(*a)
    except Exception as e:
        return f"<p style='color:crimson'>Erro ao carregar esta aba: {html.escape(str(e))}</p>"


def como_html(obj):
    if isinstance(obj, str):
        return obj
    if obj is None:
        return "<p>Sem dados para exibir.</p>"
    return obj.to_html(full_html=False, include_plotlyjs=False)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    hoje = date.today()
    ap.add_argument("--inicio", type=date.fromisoformat, default=hoje - timedelta(days=6))
    ap.add_argument("--fim", type=date.fromisoformat, default=hoje)
    ap.add_argument("--saida", default="dashboard_nasa.html")
    a = ap.parse_args()
    if a.fim < a.inicio:
        raise SystemExit("--fim deve ser >= --inicio")

    abas = [("APOD", seguro(aba_apod)),
            ("Exoplanetas", seguro(fig_exoplanetas)),
            ("Eventos naturais", seguro(fig_eventos)),
            ("Asteroides", seguro(fig_asteroides, a.inicio, a.fim))]
    botoes = "".join(f'<button onclick="show({i})" id="b{i}">{n}</button>' for i, (n, _) in enumerate(abas))
    corpos = "".join(f'<div class="tab" id="t{i}" style="display:{"block" if i == 0 else "none"}">'
                     f'{como_html(c)}</div>' for i, (_, c) in enumerate(abas))
    # plotly.js carregado uma única vez no <head>
    pagina = f"""<!doctype html><html><head><meta charset="utf-8"><title>Dashboard NASA</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>body{{font-family:sans-serif;margin:20px;background:#0b1020;color:#eee}}
button{{padding:10px 18px;margin-right:6px;border:0;border-radius:6px;cursor:pointer;background:#24305e;color:#fff}}
button.on{{background:#4c6ef5}} .tab{{margin-top:16px;background:#fff;color:#111;padding:16px;border-radius:8px}}</style>
</head><body><h1>🚀 Exploração Espacial - NASA</h1>{botoes}{corpos}
<script>function show(i){{document.querySelectorAll('.tab').forEach((e,j)=>e.style.display=i==j?'block':'none');
document.querySelectorAll('button').forEach((b,j)=>b.className=i==j?'on':'');
window.dispatchEvent(new Event('resize'));}}show(0);</script></body></html>"""
    with open(a.saida, "w", encoding="utf-8") as f:
        f.write(pagina)
    print(f"Dashboard salvo em {a.saida}")


if __name__ == "__main__":
    main()
