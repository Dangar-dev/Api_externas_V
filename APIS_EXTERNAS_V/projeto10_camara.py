#!/usr/bin/env python3
"""
Projeto 10 - Dashboard de Atividade Legislativa (API Dados Abertos da Câmara)

Desafio 1: ranking de deputados que mais apresentaram proposições sobre um tema/ano
    python projeto10_camara.py ranking --tema saúde --ano 2024

Desafio 2: heatmap de participação em votações por partido x mês
    python projeto10_camara.py heatmap --ano 2024 --max-votacoes 120

A API é pública (sem chave). Há limite de requisições, então o script usa
retry com backoff, paginação via links "next" e sessão HTTP reutilizada.
"""
import argparse
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import matplotlib.pyplot as plt
import pandas as pd
import plotly.express as px
import requests
import seaborn as sns

BASE = "https://dadosabertos.camara.leg.br/api/v2"
S = requests.Session()
S.headers.update({"Accept": "application/json", "User-Agent": "dashboard-legislativo/1.0"})


# ---------------------------------------------------------------- infraestrutura
def get(url, params=None, tentativas=5):
    """GET com retry/backoff para 429 e 5xx."""
    ultimo = None
    for i in range(tentativas):
        try:
            r = S.get(url, params=params, timeout=30)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(2 ** i)
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            ultimo = e
            time.sleep(2 ** i)
    raise RuntimeError(f"Falha em {url}: {ultimo}")


def paginar(caminho, params, max_paginas=None):
    """Percorre todas as páginas seguindo o link rel=next."""
    params = {**params, "itens": params.get("itens", 100), "pagina": 1}
    url, n = f"{BASE}{caminho}", 0
    while url:
        js = get(url, params if n == 0 else None)
        yield from js.get("dados", [])
        n += 1
        if max_paginas and n >= max_paginas:
            break
        url = next((l["href"] for l in js.get("links", []) if l["rel"] == "next"), None)


# ---------------------------------------------------------------- desafio 1
def partidos_deputados():
    """id do deputado -> (nome, partido) da legislatura atual."""
    return {d["id"]: (d["nome"], d["siglaPartido"]) for d in paginar("/deputados", {"itens": 100})}


def autores_deputados(id_proposicao):
    try:
        js = get(f"{BASE}/proposicoes/{id_proposicao}/autores")
    except RuntimeError:
        return []
    out = []
    for a in js.get("dados", []):
        m = re.search(r"/deputados/(\d+)", a.get("uri") or "")
        if m:
            out.append((int(m.group(1)), a["nome"]))
    return out


def ranking(tema, ano, top, tipos, max_paginas):
    print(f"Buscando proposições sobre '{tema}' em {ano}...")
    props = list(paginar("/proposicoes", {"ano": ano, "keywords": tema, "siglaTipo": tipos,
                                         "ordenarPor": "id", "ordem": "ASC"}, max_paginas))
    print(f"{len(props)} proposições. Buscando autores (pode levar alguns minutos)...")
    with ThreadPoolExecutor(max_workers=6) as ex:
        resultados = list(ex.map(lambda p: autores_deputados(p["id"]), props))
    cont, nomes = Counter(), {}
    for autores in resultados:
        for id_dep, nome in autores:
            cont[id_dep] += 1
            nomes[id_dep] = nome
    if not cont:
        raise SystemExit("Nenhuma proposição de autoria de deputados encontrada.")
    partidos = partidos_deputados()
    df = pd.DataFrame([{"deputado": nomes[i], "partido": partidos.get(i, ("", "—"))[1], "proposicoes": n}
                       for i, n in cont.items()])
    df["rotulo"] = df["deputado"] + " (" + df["partido"] + ")"
    df = df.sort_values("proposicoes", ascending=False).head(top)
    fig = px.bar(df.sort_values("proposicoes"), x="proposicoes", y="rotulo", color="partido",
                 orientation="h", title=f"Top {top} deputados com mais proposições sobre '{tema}' em {ano}",
                 labels={"proposicoes": "Nº de proposições", "rotulo": ""}, height=max(450, 28 * len(df)))
    arq = f"ranking_{tema}_{ano}.html".replace(" ", "_")
    fig.write_html(arq)
    df.to_csv(arq.replace(".html", ".csv"), index=False)
    print(df[["deputado", "partido", "proposicoes"]].to_string(index=False))
    print(f"Gráfico salvo em {arq}")
    fig.show()


# ---------------------------------------------------------------- desafio 2
def votos_da_votacao(v):
    try:
        js = get(f"{BASE}/votacoes/{v['id']}/votos")
    except RuntimeError:
        return []
    data = pd.to_datetime(v.get("data") or v.get("dataHoraRegistro"), errors="coerce")
    if pd.isna(data):
        return []
    linhas = []
    for x in js.get("dados", []):
        deputado = x.get("deputado_") or {}
        if not deputado.get("id"):
            continue
        linhas.append({"mes": data.to_period("M").to_timestamp(), "votacao": v["id"],
                       "deputado": deputado["id"], "partido": deputado.get("siglaPartido") or "Sem partido"})
    return linhas


def votacoes_do_ano(ano):
    """Busca votações em blocos de no máximo três meses, limite da API."""
    inicio = date(ano, 1, 1)
    fim_ano = date(ano, 12, 31)
    todas = {}
    while inicio <= fim_ano:
        fim = min(inicio + timedelta(days=89), fim_ano)
        params = {"dataInicio": inicio.isoformat(), "dataFim": fim.isoformat(),
                  "ordenarPor": "dataHoraRegistro", "ordem": "ASC", "itens": 200}
        for votacao in paginar("/votacoes", params):
            todas[votacao["id"]] = votacao
        inicio = fim + timedelta(days=1)
    return list(todas.values())


def heatmap(ano, max_votacoes, top_partidos):
    print(f"Listando votações de {ano}...")
    vots = votacoes_do_ano(ano)
    df = pd.DataFrame(vots)
    if df.empty:
        raise SystemExit("Sem votações no período.")
    df["data"] = pd.to_datetime(df["dataHoraRegistro"])
    df["mes"] = df["data"].dt.month
    # amostra equilibrada por mês (a maioria das votações é simbólica e não tem votos nominais)
    por_mes = max(1, max_votacoes // 12)
    amostra = df.groupby("mes", group_keys=False).apply(lambda g: g.sample(min(len(g), por_mes), random_state=1))
    print(f"{len(df)} votações no ano; analisando amostra de {len(amostra)}...")
    with ThreadPoolExecutor(max_workers=6) as ex:
        linhas = [l for r in ex.map(votos_da_votacao, amostra.to_dict("records")) for l in r]
    votos = pd.DataFrame(linhas)
    if votos.empty:
        raise SystemExit("A amostra não trouxe votos nominais. Aumente --max-votacoes.")
    votos["mes_n"] = votos["mes"].dt.month
    # participação = votos / (votações nominais do mês x deputados distintos do partido)
    n_vot = votos.groupby("mes_n")["votacao"].nunique()
    n_dep = votos.groupby("partido")["deputado"].nunique()
    tab = votos.groupby(["partido", "mes_n"]).size().unstack(fill_value=0)
    tab = tab.reindex(columns=range(1, 13), fill_value=0)
    part = tab.div(n_vot.reindex(range(1, 13)), axis=1).div(n_dep, axis=0) * 100
    part = part.clip(upper=100)
    top = n_dep.sort_values(ascending=False).head(top_partidos).index
    part = part.loc[top]
    part.columns = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]
    plt.figure(figsize=(12, 0.5 * len(part) + 3))
    sns.heatmap(part, annot=True, fmt=".0f", cmap="YlOrRd", cbar_kws={"label": "% de participação"})
    plt.title(f"Participação em votações nominais por partido e mês - {ano} (amostra)")
    plt.ylabel("")
    plt.tight_layout()
    arq = f"heatmap_participacao_{ano}.png"
    plt.savefig(arq, dpi=150)
    atividade = part.mean()
    print(f"Mês de maior participação: {atividade.idxmax()} | menor: {atividade.idxmin()}")
    print(f"Heatmap salvo em {arq}")
    # O resultado principal é o PNG; não bloquear o terminal esperando uma janela.
    plt.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("ranking")
    r.add_argument("--tema", default="saúde")
    r.add_argument("--ano", type=int, default=2024)
    r.add_argument("--top", type=int, default=15)
    r.add_argument("--tipos", nargs="+", default=["PL", "PLP", "PEC", "PDL"])
    r.add_argument("--max-paginas", type=int, default=10, help="limite de páginas de proposições (100/página)")
    h = sub.add_parser("heatmap")
    h.add_argument("--ano", type=int, default=2024)
    h.add_argument("--max-votacoes", type=int, default=120)
    h.add_argument("--top-partidos", type=int, default=12)
    a = ap.parse_args()
    if a.cmd == "ranking":
        ranking(a.tema, a.ano, a.top, a.tipos, a.max_paginas)
    else:
        heatmap(a.ano, a.max_votacoes, a.top_partidos)
