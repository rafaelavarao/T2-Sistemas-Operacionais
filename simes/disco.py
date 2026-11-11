# -*- coding: utf-8 -*-
"""
Modelos de dispositivo de armazenamento.

DiscoHDD  — FORNECIDO. Implementa o modelo de custo descrito em parametros.py.
DiscoFlash — BÔNUS. Armazenamento flash (SSD, celulares): implementem se quiserem o bônus.

Todo dispositivo oferece:
    atender(bloco, n_blocos, tipo) -> (duracao_ms, distancia_cilindros)
    mover(cilindro)                -> (duracao_ms, distancia_cilindros)
    cilindro_atual                 (atributo)
"""
import math
from array import array
from collections import deque

from simes import parametros as P


def cilindro_do_bloco(bloco):
    return bloco // P.BLOCOS_POR_CILINDRO


class DiscoHDD:
    nome = "hdd"

    def __init__(self, busca_minima_ms=P.BUSCA_MINIMA_MS,
                 busca_por_cilindro_ms=P.BUSCA_POR_CILINDRO_MS,
                 latencia_rotacional_ms=P.LATENCIA_ROTACIONAL_MS,
                 transferencia_ms_por_bloco=P.TRANSFERENCIA_MS_POR_BLOCO,
                 cilindros=P.CILINDROS, cilindro_inicial=0, nome=None):
        self.busca_minima_ms = busca_minima_ms
        self.busca_por_cilindro_ms = busca_por_cilindro_ms
        self.latencia_rotacional_ms = latencia_rotacional_ms
        self.transferencia_ms_por_bloco = transferencia_ms_por_bloco
        self.cilindros = cilindros
        self.cilindro_atual = cilindro_inicial
        self.ultimo_bloco = None          # último bloco transferido (para detectar sequência)
        self.deslocamento_total = 0       # cilindros percorridos pelo braço
        if nome:
            self.nome = nome

    def tempo_busca(self, distancia):
        if distancia == 0:
            return 0.0
        return self.busca_minima_ms + distancia * self.busca_por_cilindro_ms

    def mover(self, cilindro):
        """Leva o braço até um cilindro sem transferir dados (usado pelo SCAN e pelo C-SCAN)."""
        distancia = abs(cilindro - self.cilindro_atual)
        duracao = self.tempo_busca(distancia)
        self.cilindro_atual = cilindro
        self.ultimo_bloco = None
        self.deslocamento_total += distancia
        return duracao, distancia

    def atender(self, bloco, n_blocos=1, tipo="L"):
        cilindro = cilindro_do_bloco(bloco)
        distancia = abs(cilindro - self.cilindro_atual)
        duracao = self.tempo_busca(distancia)
        em_sequencia = self.ultimo_bloco is not None and bloco == self.ultimo_bloco + 1
        if not em_sequencia:
            duracao += self.latencia_rotacional_ms
        duracao += n_blocos * self.transferencia_ms_por_bloco
        ultimo = bloco + n_blocos - 1
        self.cilindro_atual = cilindro_do_bloco(ultimo)
        self.ultimo_bloco = ultimo
        self.deslocamento_total += distancia
        return duracao, distancia


def criar_disco(nome):
    """Fábrica usada pelos simuladores e pelos experimentos."""
    if nome == "hdd":
        return DiscoHDD()
    if nome == "rapido":
        return DiscoHDD(nome="rapido", **P.DISPOSITIVO_RAPIDO)
    if nome == "flash":
        return DiscoFlash()
    raise ValueError("dispositivo desconhecido: %s" % nome)


# ======================================================================
# BÔNUS — armazenamento flash
# ======================================================================
class DiscoFlash:
    """
    Simula um SSD com uma camada de tradução simples (FTL) do tipo log:

    - uma página flash = um bloco lógico de 4 KiB;
    - páginas são agrupadas em blocos apagáveis de FLASH_PAGINAS_POR_BLOCO_APAGAVEL páginas;
    - uma página só pode ser gravada se estiver apagada; apagar só é possível por bloco inteiro;
    - regravar um bloco lógico grava em uma página livre nova e invalida a antiga;
    - quando faltam páginas livres, a coleta de lixo escolhe um bloco apagável, copia as
      páginas ainda válidas para outro lugar e apaga o bloco.

    Não há busca nem latência rotacional. Tempos em parametros.py (FLASH_*).
    Indicadores que o dispositivo deve expor (atributos):
        paginas_gravadas_host      páginas pedidas pelo sistema
        paginas_programadas        páginas efetivamente gravadas (inclui cópias da coleta de lixo)
        apagamentos                blocos apagados
    Amplificação de escrita = paginas_programadas / paginas_gravadas_host.

    DECISÕES DE PROJETO (do grupo):
    - capacidade física = capacidade lógica * (1 + FLASH_AREA_RESERVA), arredondada para cima
      em blocos apagáveis (over-provisioning);
    - escrita em log: há um bloco "ativo" que recebe as páginas em ordem; cheio, ele é
      fechado e o próximo bloco livre vira o ativo;
    - a coleta de lixo roda quando sobram menos de GC_LIMIAR blocos livres; política gulosa
      (greedy): a vítima é o bloco fechado com MENOS páginas válidas. Cada página válida é
      lida e reprogramada no bloco ativo; depois a vítima é apagada;
    - o tempo da coleta de lixo é cobrado da requisição de escrita que a disparou
      (coleta "em primeiro plano", o pior caso; SSDs reais também fazem coleta em segundo plano);
    - ler um bloco lógico nunca gravado custa uma leitura de página (o controlador devolve zeros).
    """
    nome = "flash"
    GC_LIMIAR = 2

    def __init__(self, total_blocos_logicos=P.TOTAL_BLOCOS):
        ppb = P.FLASH_PAGINAS_POR_BLOCO_APAGAVEL
        self.cilindros = P.CILINDROS            # só para a interface do simulador de E/S
        self.cilindro_atual = 0
        self.deslocamento_total = 0
        self.paginas_gravadas_host = 0
        self.paginas_programadas = 0
        self.apagamentos = 0
        self.paginas_copiadas_gc = 0
        self.coletas = 0
        self.ppb = ppb
        self.total_logicos = total_blocos_logicos
        fisicas = int(math.ceil(total_blocos_logicos * (1.0 + P.FLASH_AREA_RESERVA)))
        self.n_blocos = max(self.GC_LIMIAR + 2, int(math.ceil(fisicas / float(ppb))))
        self.mapa = {}                                   # bloco lógico -> página física
        self.dono = array("i", [-1]) * (self.n_blocos * ppb)   # página física -> bloco lógico
        self.validas = [0] * self.n_blocos               # páginas válidas por bloco apagável
        self.livres = deque(range(self.n_blocos))        # blocos apagados, prontos para gravar
        self.fechados = set()                            # blocos cheios (candidatos à coleta)
        self.ativo = self.livres.popleft()
        self.ponteiro = 0                                # próxima página livre do bloco ativo
        self._tempo = 0.0

    # ----------------------------------------------------------- interface
    def mover(self, cilindro):
        return 0.0, 0

    def atender(self, bloco, n_blocos=1, tipo="L"):
        if bloco < 0 or bloco + n_blocos > self.total_logicos:
            raise ValueError("bloco fora do dispositivo: %d" % bloco)
        self._tempo = 0.0
        if tipo == "L":
            self._tempo += n_blocos * P.FLASH_LEITURA_MS
        else:
            for b in range(bloco, bloco + n_blocos):
                self.paginas_gravadas_host += 1
                self._invalidar(b)
                self._programar(b, coletando=False)
        return self._tempo, 0

    @property
    def amplificacao_escrita(self):
        return self.paginas_programadas / float(max(1, self.paginas_gravadas_host))

    def precondicionar(self, fracao, semente=1):
        """Grava `fracao` da capacidade lógica (sequencialmente) e depois a mesma quantidade
        em posições aleatórias, para deixar o dispositivo num estado "usado" (estado
        estacionário). Zera os contadores no fim. Usado só nos experimentos extras."""
        import random
        rng = random.Random(semente)
        n = int(self.total_logicos * fracao)
        for b in range(n):
            self.atender(b, 1, "E")
        for _ in range(n):
            self.atender(rng.randrange(n), 1, "E")
        self.paginas_gravadas_host = self.paginas_programadas = self.apagamentos = 0
        self.paginas_copiadas_gc = self.coletas = 0

    # ----------------------------------------------------------- FTL interna
    def _invalidar(self, logico):
        antiga = self.mapa.pop(logico, None)
        if antiga is not None:
            self.dono[antiga] = -1
            self.validas[antiga // self.ppb] -= 1

    def _programar(self, logico, coletando):
        if self.ponteiro == self.ppb:
            self._abrir_novo_ativo(coletando)
        pagina = self.ativo * self.ppb + self.ponteiro
        self.ponteiro += 1
        self.dono[pagina] = logico
        self.mapa[logico] = pagina
        self.validas[self.ativo] += 1
        self.paginas_programadas += 1
        self._tempo += P.FLASH_PROGRAMACAO_MS

    def _abrir_novo_ativo(self, coletando):
        """O bloco ativo encheu: fecha-o e abre outro (rodando a coleta, se preciso)."""
        self.fechados.add(self.ativo)
        if not coletando:
            while len(self.livres) < self.GC_LIMIAR and self._coletar():
                pass
            if self.ponteiro < self.ppb:     # as cópias da coleta já abriram um bloco novo
                return
        if not self.livres:
            raise RuntimeError("flash sem blocos livres (área de reserva esgotada)")
        self.ativo = self.livres.popleft()
        self.ponteiro = 0

    def _coletar(self):
        """Coleta de lixo gulosa. Devolve False se não há vítima que libere espaço."""
        if not self.fechados:
            return False
        vitima = min(self.fechados, key=lambda b: (self.validas[b], b))
        if self.validas[vitima] >= self.ppb:
            return False
        self.fechados.discard(vitima)
        self.coletas += 1
        inicio = vitima * self.ppb
        for pagina in range(inicio, inicio + self.ppb):
            logico = self.dono[pagina]
            if logico >= 0:                          # página válida: copiar antes de apagar
                self._tempo += P.FLASH_LEITURA_MS
                self.dono[pagina] = -1
                self.validas[vitima] -= 1
                del self.mapa[logico]
                self.paginas_copiadas_gc += 1
                self._programar(logico, coletando=True)
        self._tempo += P.FLASH_APAGAMENTO_MS
        self.apagamentos += 1
        self.livres.append(vitima)
        return True
