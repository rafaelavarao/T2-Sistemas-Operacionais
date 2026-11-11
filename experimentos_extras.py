# -*- coding: utf-8 -*-
"""
Experimentos EXTRAS do grupo (não fazem parte dos resultados oficiais de experimentos.py).

E13 — Flash em estado "usado": o X13 oficial roda num SSD novo, de 1,2 GiB, em que as cargas
gravam menos de 0,5% da capacidade; a coleta de lixo nunca roda e a amplificação de escrita
fica em 1,0. Aqui o DiscoFlash (mesmos parâmetros FLASH_* de parametros.py) é reduzido para
32.768 páginas lógicas (128 MiB), pré-condicionado (preenchido até uma fração da capacidade e
regravado aleatoriamente) e então recebe 50.000 escritas de 4 KiB, com dois padrões:
    uniforme   posições aleatórias uniformes na região ocupada;
    quente     80% das escritas em 20% da região ocupada (localidade, como em logs e metadados).
Mede-se a amplificação de escrita, os apagamentos e a latência das escritas (média e máxima:
a máxima mostra as pausas causadas pela coleta de lixo em primeiro plano).

Uso:  python experimentos_extras.py      -> resultados/extras/flash_gc.csv
                                           graficos/extra_flash_amplificacao.png (com matplotlib)
"""
import csv
import os
import random

from simes.disco import DiscoFlash
from simes.metricas import media, percentil

AQUI = os.path.dirname(os.path.abspath(__file__))
PAGINAS = 32768
ESCRITAS = 50000
OCUPACOES = (0.5, 0.7, 0.8, 0.9, 0.95)


def rodar(ocupacao, padrao, semente=2026):
    disco = DiscoFlash(total_blocos_logicos=PAGINAS)
    disco.precondicionar(ocupacao, semente=semente)
    rng = random.Random(semente + 1)
    usados = int(PAGINAS * ocupacao)
    quentes = max(1, usados // 5)
    latencias = []
    for _ in range(ESCRITAS):
        if padrao == "quente" and rng.random() < 0.8:
            bloco = rng.randrange(quentes)
        else:
            bloco = rng.randrange(usados)
        duracao, _ = disco.atender(bloco, 1, "E")
        latencias.append(duracao)
    return {
        "ocupacao": ocupacao, "padrao": padrao, "escritas_host": disco.paginas_gravadas_host,
        "paginas_programadas": disco.paginas_programadas, "apagamentos": disco.apagamentos,
        "amplificacao_escrita": round(disco.amplificacao_escrita, 4),
        "latencia_media_ms": round(media(latencias), 4),
        "latencia_p99_ms": round(percentil(latencias, 99), 4),
        "latencia_max_ms": round(max(latencias), 4),
    }


def grafico(linhas, caminho):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib ausente: gráfico não gerado")
        return
    fig, ax = plt.subplots(figsize=(7, 4))
    for padrao, marca in (("uniforme", "o-"), ("quente", "s--")):
        pts = [(l["ocupacao"] * 100, l["amplificacao_escrita"]) for l in linhas if l["padrao"] == padrao]
        ax.plot([p[0] for p in pts], [p[1] for p in pts], marca, label=padrao)
    ax.set_xlabel("ocupação lógica do dispositivo (%)")
    ax.set_ylabel("amplificação de escrita (páginas programadas / gravadas)")
    ax.set_title("Extra E13 — flash usado: amplificação de escrita × ocupação")
    ax.grid(alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(caminho, dpi=120)
    plt.close(fig)


def main():
    linhas = []
    for padrao in ("uniforme", "quente"):
        for oc in OCUPACOES:
            l = rodar(oc, padrao)
            linhas.append(l)
            print("%-8s ocupação %3.0f%%  WA %.2f  apagamentos %5d  lat. média %.3f ms  máx %.2f ms"
                  % (padrao, oc * 100, l["amplificacao_escrita"], l["apagamentos"],
                     l["latencia_media_ms"], l["latencia_max_ms"]))
    pasta = os.path.join(AQUI, "resultados", "extras")
    os.makedirs(pasta, exist_ok=True)
    with open(os.path.join(pasta, "flash_gc.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(linhas[0]))
        w.writeheader()
        w.writerows(linhas)
    os.makedirs(os.path.join(AQUI, "graficos"), exist_ok=True)
    grafico(linhas, os.path.join(AQUI, "graficos", "extra_flash_amplificacao.png"))
    print("Gravado em resultados/extras/flash_gc.csv")


if __name__ == "__main__":
    main()
