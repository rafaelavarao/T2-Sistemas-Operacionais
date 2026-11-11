# -*- coding: utf-8 -*-
"""
Módulo M1 — cache de blocos (buffer cache).

Referência: Silberschatz, 9ª ed., 13.4.3 (Caching) e 12.6 (Efficiency and Performance) — pesquisa.

Implementem CacheLRU e CacheFIFO com a interface abaixo. O simulador de E/S chama:

    ler(bloco) -> bool
        True se o bloco está na cache (acerto). Num acerto, a LRU atualiza a recência;
        a FIFO não muda nada.
    inserir(bloco) -> list[int]
        Chamado depois de uma falta de leitura, quando o bloco chega do disco. Se a cache
        estiver cheia, expulsa uma vítima (LRU: a usada há mais tempo; FIFO: a que entrou
        primeiro). Devolve os blocos SUJOS expulsos, que o simulador vai gravar no disco.
    escrever(bloco) -> list[int]
        Escrita do usuário. Coloca (ou atualiza) o bloco na cache e devolve a lista de blocos
        que precisam ser gravados no disco AGORA:
          write-through: o próprio bloco (+ sujos expulsos, que não existem nesta política);
          write-back:    só os blocos sujos expulsos; o bloco escrito fica marcado como sujo.
        Uma escrita conta como acesso para a LRU.
    sincronizar() -> list[int]
        Gravação periódica (a cada INTERVALO_SYNC_MS): devolve todos os blocos sujos e os
        marca como limpos.
    sujos() -> int
        Quantos blocos estão sujos agora (usado para medir a perda numa queda de energia).

Política de escrita: 'write-through' ou 'write-back'.
"""
from collections import OrderedDict


class Cache:
    nome = "?"

    def __init__(self, capacidade, escrita="write-back"):
        if escrita not in ("write-through", "write-back"):
            raise ValueError("escrita deve ser 'write-through' ou 'write-back'")
        self.capacidade = capacidade
        self.escrita = escrita

    def ler(self, bloco):
        raise NotImplementedError

    def inserir(self, bloco):
        raise NotImplementedError

    def escrever(self, bloco):
        raise NotImplementedError

    def sincronizar(self):
        raise NotImplementedError

    def sujos(self):
        raise NotImplementedError


class _CacheOrdenada(Cache):
    """
    Base comum da LRU e da FIFO: um OrderedDict bloco -> sujo (bool). O primeiro item é
    sempre a próxima vítima. A única diferença entre as duas políticas é se um acerto
    (leitura ou escrita de bloco já presente) move o bloco para o fim da ordem.
    """
    atualiza_no_acerto = False

    def __init__(self, capacidade, escrita="write-back"):
        super().__init__(capacidade, escrita)
        if capacidade < 1:
            raise ValueError("a capacidade deve ser de pelo menos 1 bloco")
        self.blocos = OrderedDict()
        self.expulsoes = 0

    def _tocar(self, bloco):
        if self.atualiza_no_acerto:
            self.blocos.move_to_end(bloco)

    def _abrir_espaco(self):
        """Expulsa vítimas até haver lugar para um bloco novo; devolve as sujas expulsas."""
        sujos = []
        while len(self.blocos) >= self.capacidade:
            vitima, sujo = self.blocos.popitem(last=False)
            self.expulsoes += 1
            if sujo:
                sujos.append(vitima)
        return sujos

    def ler(self, bloco):
        if bloco in self.blocos:
            self._tocar(bloco)
            return True
        return False

    def inserir(self, bloco):
        if bloco in self.blocos:            # já chegou por outra requisição
            self._tocar(bloco)
            return []
        sujos = self._abrir_espaco()
        self.blocos[bloco] = False
        return sujos

    def escrever(self, bloco):
        write_back = self.escrita == "write-back"
        if bloco in self.blocos:
            self._tocar(bloco)
            self.blocos[bloco] = self.blocos[bloco] or write_back
            sujos = []
        else:
            sujos = self._abrir_espaco()
            self.blocos[bloco] = write_back
        if write_back:
            return sujos
        return [bloco] + sujos              # write-through: nunca há sujos, mas por garantia

    def sincronizar(self):
        sujos = [b for b, sujo in self.blocos.items() if sujo]
        for b in sujos:
            self.blocos[b] = False
        return sujos

    def sujos(self):
        return sum(1 for sujo in self.blocos.values() if sujo)


class CacheLRU(_CacheOrdenada):
    """Least Recently Used: todo acesso (leitura ou escrita) leva o bloco para o fim."""
    nome = "LRU"
    atualiza_no_acerto = True


class CacheFIFO(_CacheOrdenada):
    """First-In, First-Out: a ordem é a de entrada na cache; acertos não mudam nada."""
    nome = "FIFO"
    atualiza_no_acerto = False


CACHES = {"LRU": CacheLRU, "FIFO": CacheFIFO}


def criar_cache(nome, capacidade, escrita):
    try:
        return CACHES[nome](capacidade, escrita)
    except KeyError:
        raise ValueError("cache desconhecida: %s" % nome)
