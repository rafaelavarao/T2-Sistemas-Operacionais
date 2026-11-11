# -*- coding: utf-8 -*-
"""Testes acrescentados pelo grupo: casos especiais das implementações."""
import unittest

from simes import parametros as P
from simes.arquivos.alocacao import AlocadorIndexado
from simes.arquivos.estruturas import FCB, ErroSA
from simes.arquivos.simulador import montar_sistema
from simes.disco import DiscoFlash
from simes.es.buffering import simular_buffering
from simes.es.cache import CacheFIFO, CacheLRU
from simes.es.escalonadores import CLOOK, LOOK, SCAN, SSTF, percorrer
from simes.es.requisicao import Requisicao


def req(i, cil, chegada=0.0, tipo="L"):
    return Requisicao(i, chegada, "P1", tipo, cil * P.BLOCOS_POR_CILINDRO)


class TesteEscalonadoresGrupo(unittest.TestCase):
    def test_empate_vence_a_mais_antiga(self):
        fila = [req(0, 60), req(1, 40)]               # ambas a 10 cilindros da cabeça (50)
        self.assertEqual(SSTF().escolher(fila, 50, +1, 0.0, 199)[0], 0)

    def test_mesmo_cilindro_conta_a_frente(self):
        fila = [req(0, 10), req(1, 50)]
        i, direcao, passar = SCAN().escolher(fila, 50, -1, 0.0, 199)
        self.assertEqual((i, direcao, passar), (1, -1, []))

    def test_scan_na_borda_nao_anda(self):
        ordem, total = percorrer(SCAN(), [10, 20], 0, direcao=-1)
        self.assertEqual((ordem, total), ([10, 20], 20))

    def test_look_e_clook_fila_unitaria(self):
        self.assertEqual(percorrer(LOOK(), [5], 50, direcao=+1), ([5], 45))
        self.assertEqual(percorrer(CLOOK(), [5], 50), ([5], 45))


class TesteCacheGrupo(unittest.TestCase):
    def test_writeback_reescrita_nao_duplica_sujo(self):
        c = CacheLRU(2, "write-back")
        c.escrever(1)
        c.escrever(1)
        self.assertEqual(c.sujos(), 1)
        self.assertEqual(c.sincronizar(), [1])
        self.assertEqual(c.sincronizar(), [])

    def test_fifo_escrita_em_bloco_presente_nao_muda_ordem(self):
        c = CacheFIFO(2, "write-back")
        c.inserir(1)
        c.inserir(2)
        c.escrever(1)                        # 1 continua sendo o mais antigo
        self.assertEqual(c.inserir(3), [1])  # e sai sujo


class TesteBufferingGrupo(unittest.TestCase):
    def test_produtor_lento_dispositivo_ocioso(self):
        r = simular_buffering(3, 8.0, 2.0, 10)
        self.assertAlmostEqual(r["tempo_total_ms"], 10 * 8.0 + 2.0)
        self.assertAlmostEqual(r["bloqueio_produtor_ms"], 0.0)


class TesteSistemaArquivosGrupo(unittest.TestCase):
    def test_hard_link_mantem_blocos_ate_o_ultimo(self):
        sa = montar_sistema("contigua", total_blocos=5000)
        sa.mkdir("P1", "/d")
        sa.create("P1", "/d/a", 8192)
        livres = sa.alocador.bitmap.livres()
        sa.link("P1", "/d/a", "/d/b")
        sa.unlink("P1", "/d/a")
        self.assertEqual(sa.alocador.bitmap.livres(), livres)
        sa.unlink("P1", "/d/b")
        self.assertEqual(sa.alocador.bitmap.livres(), livres + 2)

    def test_link_simbolico_no_meio_do_caminho(self):
        sa = montar_sistema("contigua", total_blocos=5000)
        sa.mkdir("P1", "/x")
        sa.mkdir("P1", "/x/y")
        sa.create("P1", "/x/y/f", 10)
        sa.symlink("P1", "/x/y", "/atalho")
        self.assertIs(sa.resolver("/atalho/f"), sa.resolver("/x/y/f"))
        self.assertEqual(sa.resolver("/atalho", seguir_simbolico=False).tipo, "simbolico")

    def test_handle_duplicado_e_modo_invalido(self):
        sa = montar_sistema("contigua", total_blocos=5000)
        sa.create("P1", "/f", 10)
        sa.open("P1", "/f", "r", "h")
        with self.assertRaises(ErroSA):
            sa.open("P1", "/f", "r", "h")
        with self.assertRaises(ErroSA):
            sa.open("P1", "/f", "x", "h2")


class TesteAlocacaoGrupo(unittest.TestCase):
    def test_indexado_passa_para_dois_niveis_ao_estender(self):
        a = AlocadorIndexado(5000)
        f = FCB(1, "f", "arquivo", 0)
        a.alocar(f, 1000)
        self.assertEqual(len(f.extra["indices"]), 1)
        extras = a.estender(f, 100)                     # 1100 blocos: 1 + 2 índices
        self.assertEqual(len(f.extra["indices"]), 3)
        self.assertEqual(len(extras), 3)
        livres = a.bitmap.livres()
        a.liberar(f)
        self.assertEqual(a.bitmap.livres(), livres + 1100 + 3)


class TesteFlash(unittest.TestCase):
    def test_sem_coleta_quando_ha_espaco(self):
        d = DiscoFlash(total_blocos_logicos=64 * 10)
        t, _ = d.atender(0, 4, "E")
        self.assertAlmostEqual(t, 4 * P.FLASH_PROGRAMACAO_MS)
        self.assertEqual((d.paginas_programadas, d.apagamentos), (4, 0))

    def test_coleta_de_lixo_e_consistencia(self):
        d = DiscoFlash(total_blocos_logicos=64 * 20)
        d.precondicionar(0.9, semente=3)
        for k in range(5000):
            d.atender((k * 7919) % int(64 * 20 * 0.9), 1, "E")
        self.assertGreater(d.apagamentos, 0)
        self.assertGreater(d.amplificacao_escrita, 1.0)
        self.assertEqual(len(d.mapa), sum(d.validas))     # mapa e contadores coerentes
        for logico, pagina in d.mapa.items():
            self.assertEqual(d.dono[pagina], logico)


if __name__ == "__main__":
    unittest.main()
