# -*- coding: utf-8 -*-
"""
Sistema de arquivos simulado (camada 3).

Referência: Silberschatz, 9ª ed., cap. 11 — operações, FCB, tabelas de arquivos abertos,
métodos de acesso, estrutura de diretórios, links e proteção.

O que já vem pronto (FORNECIDO): a criação da raiz, as tabelas e os métodos auxiliares
(nome começando com _). Implementem os métodos marcados com TODO.

=====================================================================================
REGRAS DE CUSTO (iguais para todos os grupos — sigam à risca)
=====================================================================================
Um acesso = um bloco lido ou gravado (use _ler, _escrever e _aplicar; eles contam tudo).

Diretórios: cada diretório ocupa 1 bloco (o bloco 0 do diretório, obtido com
_bloco_do_diretorio). Ler um diretório = 1 leitura desse bloco.

resolver("/a/b/c")   lê os diretórios "/", "/a" e "/a/b" (3 leituras) e devolve o FCB de c.
                     resolver("/") devolve a raiz sem ler nada.
Operações que criam, removem ou alteram uma entrada leem os diretórios do caminho até o
pai (inclusive), exatamente como resolver, e depois:

mkdir(p)             + aloca 1 bloco para o novo diretório, grava o bloco do novo diretório
                       (1 escrita) e o bloco do pai (1 escrita)
create(p, tamanho)   + aloca ceil(tamanho / TAMANHO_BLOCO) blocos (conteúdo não é gravado)
                       e grava o bloco do pai (1 escrita)
open(p, modo, h)     lê os diretórios como resolver; nenhuma escrita
read(h, n)           lê os blocos que contêm os bytes [posição, posição + n), limitados ao
                     tamanho do arquivo; avança a posição; devolve os bytes lidos
write(h, n)          se precisar, estende o arquivo (alocador.estender); grava os blocos que
                     contêm [posição, posição + n); avança a posição; atualiza o tamanho e
                     marca o FCB como sujo. No modo 'a', a escrita começa no fim do arquivo.
seek(h, posicao)     muda a posição; nenhum acesso
close(h)             se o FCB está sujo, grava o bloco do diretório pai (1 escrita) e limpa
                     o "sujo"; atualiza as tabelas
unlink(p)            + grava o bloco do pai (1 escrita). Se o arquivo ficar sem links e
                     ninguém o tiver aberto, libera os blocos; se estiver aberto, a liberação
                     acontece no último close.

Tabelas de arquivos abertos:
- self.tabela_global: dict fcb_id -> EntradaGlobal. Uma entrada por arquivo aberto; o
  contador soma as aberturas de todos os processos. A entrada sai quando o contador zera.
- self.tabelas_processo: dict processo -> {handle: EntradaProcesso}. Uma entrada por
  abertura, com a posição e o modo.

Modos de abertura: 'r' (só leitura), 'w' (só escrita), 'rw' (ambos), 'a' (acréscimo, só
escrita). Ler com um handle sem 'r', ou escrever sem 'w'/'a': ErroSA('EBADF').
Erros: lancem ErroSA com o código adequado (veja estruturas.py).

Ganchos dos módulos (já chamados pelas operações do núcleo, que vocês implementam):
- _verificar_permissao(processo, fcb, modo): no núcleo não faz nada; o M5 implementa.
- _verificar_trava(processo, fcb):            no núcleo não faz nada; o M5 implementa.
  O núcleo deve chamar _verificar_permissao no open e _verificar_trava no read e no write.
"""
import math
from collections import Counter

from simes import parametros as P
from simes.arquivos.estruturas import FCB, EntradaGlobal, EntradaProcesso, ErroSA

MAX_LINKS_SIMBOLICOS = 8


class SistemaArquivos:

    def __init__(self, camada, alocador, bloqueio="consultivo"):
        self.camada = camada
        self.alocador = alocador
        self.bloqueio = bloqueio            # 'consultivo' ou 'mandatorio' (M5)
        self.fcbs = {}
        self._proximo_id = 0
        self.agora_ms = 0.0                 # atualizado pelo simulador antes de cada operação
        self.tabela_global = {}             # fcb_id -> EntradaGlobal
        self.tabelas_processo = {}          # processo -> {handle: EntradaProcesso}
        self.usuarios = {}                  # processo -> nome do usuário (M5); padrão: 'root'
        self.contadores = Counter()         # indicadores extras dos módulos (vão para o CSV)
        self._link_pendente = False         # a última resolução falhou num link sem alvo (M4)
        self.raiz = self._novo_fcb("/", "diretorio", None)
        self.alocador.alocar(self.raiz, 1)  # a raiz já existe ao "formatar": sem custo

    # ================================================================ FORNECIDO
    def _novo_fcb(self, nome, tipo, pai, processo=None):
        """Cria um FCB (dono = usuário do processo) e o registra em self.fcbs. Não aloca blocos."""
        fcb = FCB(self._proximo_id, nome, tipo, pai, self.agora_ms, self.usuario_de(processo))
        self.fcbs[fcb.id] = fcb
        self._proximo_id += 1
        return fcb

    def usuario_de(self, processo):
        return self.usuarios.get(processo, "root")

    def _ler(self, blocos):
        self.camada.ler(blocos)

    def _escrever(self, blocos):
        self.camada.escrever(blocos)

    def _aplicar(self, extras):
        """Executa a lista de acessos extras devolvida pelo alocador."""
        for tipo, bloco in extras:
            if tipo == "L":
                self._ler([bloco])
            else:
                self._escrever([bloco])

    def _bloco_do_diretorio(self, diretorio):
        """Bloco físico onde está o conteúdo do diretório (já executa os extras do alocador)."""
        bloco, extras = self.alocador.bloco_fisico(diretorio, 0)
        self._aplicar(extras)
        return bloco

    def _ler_diretorio(self, diretorio):
        self._ler([self._bloco_do_diretorio(diretorio)])

    def _gravar_diretorio(self, diretorio):
        self._escrever([self._bloco_do_diretorio(diretorio)])

    def _blocos_do_intervalo(self, fcb, inicio, fim):
        """Blocos físicos que contêm os bytes [inicio, fim) do arquivo (executa os extras)."""
        if fim <= inicio:
            return []
        primeiro = inicio // P.TAMANHO_BLOCO
        ultimo = (fim - 1) // P.TAMANHO_BLOCO
        blocos = []
        for i in range(primeiro, ultimo + 1):
            bloco, extras = self.alocador.bloco_fisico(fcb, i)
            self._aplicar(extras)
            blocos.append(bloco)
        return blocos

    @staticmethod
    def _blocos_necessarios(tamanho):
        return int(math.ceil(tamanho / float(P.TAMANHO_BLOCO)))

    @staticmethod
    def _dividir(caminho):
        if not caminho.startswith("/"):
            raise ErroSA("EINVAL", "o caminho deve ser absoluto: %s" % caminho)
        return [p for p in caminho.split("/") if p]

    def _entrada(self, processo, handle):
        try:
            return self.tabelas_processo[processo][handle]
        except KeyError:
            raise ErroSA("EBADF", "%s não tem o handle %s" % (processo, handle))

    def _liberar_arquivo(self, fcb):
        self.alocador.liberar(fcb)
        del self.fcbs[fcb.id]

    def _verificar_permissao(self, processo, fcb, modo):
        """Gancho do M5. No núcleo, tudo é permitido."""
        return None

    def _verificar_trava(self, processo, fcb):
        """Gancho do M5. No núcleo, travas não existem."""
        return None

    # ============================================================ núcleo (N3)
    MODOS = ("r", "w", "rw", "a")

    @staticmethod
    def _pode_ler(modo):
        return "r" in modo

    @staticmethod
    def _pode_escrever(modo):
        return "w" in modo or modo == "a"

    def resolver(self, caminho, seguir_simbolico=True):
        """Devolve o FCB do caminho absoluto, lendo os diretórios do caminho (ver regras).
        Erros: ENOENT, ENOTDIR. Com o M4: segue links simbólicos (inclusive no meio do
        caminho; o último componente só se seguir_simbolico=True); ELOOP depois de
        MAX_LINKS_SIMBOLICOS links."""
        self._link_pendente = False
        fcb, _ = self._resolver(self.raiz, self._dividir(caminho), seguir_simbolico, 0)
        return fcb

    def _resolver(self, inicio, partes, seguir_ultimo, links_seguidos):
        """Percorre `partes` a partir do diretório `inicio`. Lê cada diretório antes de
        procurar o componente nele. Devolve (fcb, links_seguidos)."""
        atual = inicio
        for k, nome in enumerate(partes):
            if atual.tipo != "diretorio":
                raise ErroSA("ENOTDIR", "%s não é diretório" % atual.nome)
            self._ler_diretorio(atual)
            if nome not in atual.entradas:
                raise ErroSA("ENOENT", nome)
            proximo = self.fcbs[atual.entradas[nome]]
            ultimo = k == len(partes) - 1
            if proximo.tipo == "simbolico" and (not ultimo or seguir_ultimo):
                links_seguidos += 1
                if links_seguidos > MAX_LINKS_SIMBOLICOS:
                    raise ErroSA("ELOOP", "links simbólicos demais em %s" % nome)
                base = self.raiz if proximo.alvo.startswith("/") else atual
                alvo = [p for p in proximo.alvo.split("/") if p]
                try:
                    proximo, links_seguidos = self._resolver(base, alvo, True, links_seguidos)
                except ErroSA as e:
                    if e.codigo == "ENOENT":
                        self._link_pendente = True       # o alvo do link não existe
                    raise
            atual = proximo
        return atual, links_seguidos

    def _localizar_pai(self, caminho):
        """Lê os diretórios até o pai (inclusive), como resolver(caminho) faria, e devolve
        (fcb_do_pai, nome_do_último_componente)."""
        partes = self._dividir(caminho)
        if not partes:
            raise ErroSA("EEXIST", "a raiz já existe")
        self._link_pendente = False
        pai, _ = self._resolver(self.raiz, partes[:-1], True, 0)
        if pai.tipo != "diretorio":
            raise ErroSA("ENOTDIR", "%s não é diretório" % pai.nome)
        self._ler_diretorio(pai)
        return pai, partes[-1]

    def _criar_entrada(self, processo, caminho, tipo, n_blocos):
        """Cria um FCB novo no pai de `caminho` e aloca n_blocos (None = não aloca)."""
        pai, nome = self._localizar_pai(caminho)
        if nome in pai.entradas:
            raise ErroSA("EEXIST", caminho)
        fcb = self._novo_fcb(nome, tipo, pai.id, processo)
        if n_blocos is not None:
            try:
                extras = self.alocador.alocar(fcb, n_blocos)
            except ErroSA:
                del self.fcbs[fcb.id]
                raise
            self._aplicar(extras)
        pai.entradas[nome] = fcb.id
        return pai, fcb

    def mkdir(self, processo, caminho):
        pai, novo = self._criar_entrada(processo, caminho, "diretorio", 1)
        self._gravar_diretorio(novo)
        self._gravar_diretorio(pai)

    def create(self, processo, caminho, tamanho):
        if tamanho < 0:
            raise ErroSA("EINVAL", "tamanho negativo")
        pai, fcb = self._criar_entrada(processo, caminho, "arquivo", self._blocos_necessarios(tamanho))
        fcb.tamanho = tamanho
        self._gravar_diretorio(pai)

    def open(self, processo, caminho, modo, handle):
        if modo not in self.MODOS:
            raise ErroSA("EINVAL", "modo inválido: %s" % modo)
        try:
            fcb = self.resolver(caminho)
        except ErroSA as e:
            if e.codigo == "ENOENT" and self._link_pendente:
                self.contadores["links_pendentes"] += 1
            raise
        if fcb.tipo == "diretorio":
            raise ErroSA("EISDIR", caminho)
        tabela = self.tabelas_processo.setdefault(processo, {})
        if handle in tabela:
            raise ErroSA("EBADF", "%s já usa o handle %s" % (processo, handle))
        self._verificar_permissao(processo, fcb, modo)
        entrada = self.tabela_global.get(fcb.id)
        if entrada is None:
            entrada = self.tabela_global[fcb.id] = EntradaGlobal(fcb.id)
        entrada.contador += 1
        tabela[handle] = EntradaProcesso(fcb.id, modo, 0)

    def read(self, processo, handle, n):
        e = self._entrada(processo, handle)
        if not self._pode_ler(e.modo):
            raise ErroSA("EBADF", "handle %s não foi aberto para leitura" % handle)
        if n < 0:
            raise ErroSA("EINVAL", "n negativo")
        fcb = self.fcbs[e.fcb_id]
        self._verificar_trava(processo, fcb)
        fim = min(e.posicao + n, fcb.tamanho)
        if fim <= e.posicao:
            return 0
        self._ler(self._blocos_do_intervalo(fcb, e.posicao, fim))
        lidos = fim - e.posicao
        e.posicao = fim
        return lidos

    def write(self, processo, handle, n):
        e = self._entrada(processo, handle)
        if not self._pode_escrever(e.modo):
            raise ErroSA("EBADF", "handle %s não foi aberto para escrita" % handle)
        if n < 0:
            raise ErroSA("EINVAL", "n negativo")
        fcb = self.fcbs[e.fcb_id]
        self._verificar_trava(processo, fcb)
        if e.modo == "a":
            e.posicao = fcb.tamanho
        if n == 0:
            return 0
        inicio, fim = e.posicao, e.posicao + n
        faltam = self._blocos_necessarios(fim) - len(fcb.blocos)
        if faltam > 0:
            self._aplicar(self.alocador.estender(fcb, faltam))
        self._escrever(self._blocos_do_intervalo(fcb, inicio, fim))
        e.posicao = fim
        fcb.tamanho = max(fcb.tamanho, fim)
        fcb.modificado_ms = self.agora_ms
        fcb.sujo = True
        return n

    def seek(self, processo, handle, posicao):
        e = self._entrada(processo, handle)
        if not isinstance(posicao, int) or posicao < 0:
            raise ErroSA("EINVAL", "posição inválida: %r" % (posicao,))
        e.posicao = posicao

    def close(self, processo, handle):
        e = self._entrada(processo, handle)
        fcb = self.fcbs[e.fcb_id]
        if fcb.sujo:
            pai = self.fcbs.get(fcb.pai)
            if pai is not None:
                self._gravar_diretorio(pai)       # metadados (tamanho, datas) ficam no pai
            fcb.sujo = False
        del self.tabelas_processo[processo][handle]
        entrada = self.tabela_global[fcb.id]
        if entrada.trava == processo:
            entrada.trava = None
        entrada.contador -= 1
        if entrada.contador == 0:
            del self.tabela_global[fcb.id]
            if fcb.links == 0:                    # unlink pendente: libera no último close
                self._liberar_arquivo(fcb)

    def unlink(self, processo, caminho):
        pai, nome = self._localizar_pai(caminho)
        if nome not in pai.entradas:
            raise ErroSA("ENOENT", caminho)
        fcb = self.fcbs[pai.entradas[nome]]
        if fcb.tipo == "diretorio":
            raise ErroSA("EISDIR", "unlink não remove diretórios: %s" % caminho)
        del pai.entradas[nome]
        fcb.links -= 1
        self._gravar_diretorio(pai)
        if fcb.links == 0 and fcb.id not in self.tabela_global:
            self._liberar_arquivo(fcb)

    # ================================================================ módulo M4
    def link(self, processo, existente, novo):
        """Hard link: lê os diretórios até `existente` (como resolver) e até o pai de `novo`;
        cria a entrada; links += 1; grava o bloco do pai de `novo` (1 escrita).
        Hard link para diretório: ErroSA('EPERM')."""
        fcb = self.resolver(existente, seguir_simbolico=False)   # como o link(2) do Linux
        if fcb.tipo == "diretorio":
            raise ErroSA("EPERM", "hard link para diretório: %s" % existente)
        pai, nome = self._localizar_pai(novo)
        if nome in pai.entradas:
            raise ErroSA("EEXIST", novo)
        pai.entradas[nome] = fcb.id
        fcb.links += 1
        self._gravar_diretorio(pai)

    def symlink(self, processo, alvo, caminho):
        """Link simbólico: lê os diretórios até o pai de `caminho`; cria um FCB do tipo
        'simbolico' com fcb.alvo = alvo (sem blocos de dados); grava o bloco do pai (1 escrita).
        O alvo não precisa existir."""
        pai, fcb = self._criar_entrada(processo, caminho, "simbolico", None)
        fcb.alvo = str(alvo)
        self._gravar_diretorio(pai)

    def percorrer(self, processo, caminho):
        """Percorre a árvore a partir de `caminho` (como um `find`), lendo cada diretório
        visitado (1 leitura) e seguindo links simbólicos para diretórios. Não pode entrar em
        laço: ao chegar de novo a um diretório já visitado nesta travessia, conte
        self.contadores['ciclos_detectados'] e não desça. Contem também
        'percorrer_diretorios', 'percorrer_arquivos' e 'links_pendentes' (links simbólicos
        cujo alvo não existe; contem também os encontrados pelo open)."""
        inicio = self.resolver(caminho)
        if inicio.tipo != "diretorio":
            self.contadores["percorrer_arquivos"] += 1
            return
        visitados = set()
        pilha = [inicio]                           # busca em profundidade, sem recursão
        while pilha:
            d = pilha.pop()
            if d.id in visitados:
                self.contadores["ciclos_detectados"] += 1
                continue
            visitados.add(d.id)
            self._ler_diretorio(d)
            self.contadores["percorrer_diretorios"] += 1
            filhos = []
            for nome in sorted(d.entradas):
                f = self.fcbs[d.entradas[nome]]
                if f.tipo == "simbolico":
                    try:
                        f = self.resolver(f.alvo)      # seguir o link custa resolver o alvo
                    except ErroSA as e:
                        if e.codigo == "ENOENT":
                            self.contadores["links_pendentes"] += 1
                        elif e.codigo == "ELOOP":
                            self.contadores["links_em_laco"] += 1
                        continue
                if f.tipo == "diretorio":
                    filhos.append(f)
                else:
                    self.contadores["percorrer_arquivos"] += 1
            pilha.extend(reversed(filhos))         # visita na ordem alfabética

    # ================================================================ módulo M5
    _EXIGIDAS = {"r": "r", "w": "w", "a": "w", "rw": "rw"}

    @staticmethod
    def _rwx(bits):
        return ("r" if bits & 4 else "-") + ("w" if bits & 2 else "-") + ("x" if bits & 1 else "-")

    def _verificar_permissao(self, processo, fcb, modo):
        usuario = self.usuario_de(processo)
        if usuario == "root":
            self.contadores["verificacoes"] += 1
            return
        concedidas = None
        for pos, (quem, perms) in enumerate(fcb.acl, start=1):
            if quem == usuario:                    # vale a PRIMEIRA entrada do usuário
                self.contadores["verificacoes"] += pos
                concedidas = perms
                break
        if concedidas is None:
            self.contadores["verificacoes"] += len(fcb.acl) + 1
            bits = (fcb.permissoes >> 6) & 7 if usuario == fcb.dono else fcb.permissoes & 7
            concedidas = self._rwx(bits)
        if any(p not in concedidas for p in self._EXIGIDAS[modo]):
            self.contadores["negados"] += 1
            raise ErroSA("EACCES", "%s não pode abrir %s em modo %s" % (usuario, fcb.nome, modo))

    def _verificar_trava(self, processo, fcb):
        entrada = self.tabela_global.get(fcb.id)
        if entrada is None or entrada.trava is None or entrada.trava == processo:
            return
        if self.bloqueio == "mandatorio":
            self.contadores["bloqueados_mandatorio"] += 1
            raise ErroSA("EBUSY", "%s está travado por %s" % (fcb.nome, entrada.trava))
        self.contadores["violacoes_consultivo"] += 1

    def usuario(self, processo, nome):
        """Associa o processo a um usuário (nenhum acesso ao disco)."""
        self.usuarios[processo] = str(nome)

    def _alterar_protecao(self, processo, caminho):
        """Parte comum de chmod e setacl: lê até o arquivo, confere o dono, devolve (pai, fcb)."""
        pai, nome = self._localizar_pai(caminho)
        if nome not in pai.entradas:
            raise ErroSA("ENOENT", caminho)
        fcb = self.fcbs[pai.entradas[nome]]
        usuario = self.usuario_de(processo)
        if usuario != "root" and usuario != fcb.dono:
            raise ErroSA("EPERM", "%s não é dono de %s" % (usuario, caminho))
        return pai, fcb

    def chmod(self, processo, caminho, permissoes):
        """Lê os diretórios até o arquivo; só o dono ou root (senão EPERM); grava o bloco do
        pai (1 escrita). permissoes em octal, como texto (ex.: '640')."""
        try:
            bits = int(str(permissoes), 8)
        except ValueError:
            raise ErroSA("EINVAL", "permissões inválidas: %s" % permissoes)
        pai, fcb = self._alterar_protecao(processo, caminho)
        fcb.permissoes = bits
        self._gravar_diretorio(pai)

    def setacl(self, processo, caminho, usuario, permissoes):
        """Como o chmod, mas acrescenta (ou substitui) a entrada (usuario, 'rwx') na ACL."""
        perms = str(permissoes)
        if len(perms) != 3 or any(c not in "rwx-" for c in perms):
            raise ErroSA("EINVAL", "permissões inválidas: %s" % perms)
        pai, fcb = self._alterar_protecao(processo, caminho)
        usuario = str(usuario)
        for k, (quem, _) in enumerate(fcb.acl):
            if quem == usuario:
                fcb.acl[k] = (usuario, perms)
                break
        else:
            fcb.acl.append((usuario, perms))
        self._gravar_diretorio(pai)

    def lock(self, processo, handle):
        """Trava exclusiva do arquivo inteiro. Se outro processo detém a trava: EBUSY
        (conte 'travas_negadas'); senão conte 'travas_concedidas'."""
        e = self._entrada(processo, handle)
        entrada = self.tabela_global[e.fcb_id]
        if entrada.trava is not None and entrada.trava != processo:
            self.contadores["travas_negadas"] += 1
            raise ErroSA("EBUSY", "travado por %s" % entrada.trava)
        entrada.trava = processo
        self.contadores["travas_concedidas"] += 1

    def unlock(self, processo, handle):
        e = self._entrada(processo, handle)
        entrada = self.tabela_global[e.fcb_id]
        if entrada.trava == processo:
            entrada.trava = None
