"""Regras de cálculo do Mídia SW — implementação ÚNICA.

Telas, chat e Excel exportado usam estas funções. Não duplique a conta em
outro lugar (ver "Agregação duplicada diverge" no handoff). A conferência
independente fica em tests/conferencia.py, escrita de outro jeito de propósito.

Regras:
- Realizado = PIs com mês <= mês fechado. Projeção = meses > mês fechado.
- Mês fechado é gravado; na primeira leitura de um ano sem valor grava 0 e congela.
  Nunca vem do calendário.
- Livre = soma da linha LIVRE (faturável) da projeção nos meses abertos. Não é resíduo.
- Totais do consolidado somam por cliente.
- Reaplicação nunca entra em expectativa, livre ou meta.
- Canonização na leitura; nomes comparados sem caixa e sem acento.
"""
import datetime
import json
from collections import defaultdict

from .db import get_config, set_config
from .util import chave, LIVRE, eh_livre

TIPOS = ('FAT', 'REAP')


def zeros():
    return [0] * 12


# ---------------- mês fechado ----------------

def mes_fechado(conn, ano):
    v = get_config(conn, f'mes_fechado:{ano}')
    if v is None:
        # primeira leitura: grava e congela. Nunca cai no calendário.
        set_config(conn, f'mes_fechado:{ano}', 0)
        return 0
    return int(v)


def pode_fechar(ano, mes, hoje=None):
    """Só pode fechar mês que já terminou."""
    hoje = hoje or datetime.date.today()
    if mes == 0:
        return True
    if not 1 <= mes <= 12:
        return False
    return (ano, mes) < (hoje.year, hoje.month)


# ---------------- de-para ----------------

class Depara:
    def __init__(self, conn):
        self.veic_por_chave = {}      # chave da grafia -> canônico
        self.veic_canon = {}          # chave do canônico -> nome de exibição
        self.veic_linhas = []
        for r in conn.execute('SELECT * FROM veiculo ORDER BY id'):
            self.veic_linhas.append(dict(r))
            disp = self.veic_canon.setdefault(chave(r['canonico']), r['canonico'])
            self.veic_por_chave[r['chave']] = disp
        self.cli_por_chave = {}
        self.cli_canon = {}
        self.cli_info = {}            # canônico -> {diretor_email, ativo, grupo, ...}
        for r in conn.execute('SELECT * FROM cliente ORDER BY id'):
            disp = self.cli_canon.setdefault(chave(r['canonico']), r['canonico'])
            self.cli_por_chave[r['chave']] = disp
            info = self.cli_info.get(disp)
            # atributos vêm da linha cujo nome bruto é o próprio canônico, senão da primeira
            if info is None or r['chave'] == chave(disp):
                self.cli_info[disp] = {'diretor_email': (r['diretor_email'] or '').lower() or None,
                                       'ativo': bool(r['ativo']), 'grupo': r['grupo'] or None,
                                       'grupo_negociacao': r['grupo_negociacao'] or None}

    def veiculo(self, nome):
        k = chave(nome)
        if k == 'livre':
            return LIVRE
        return self.veic_por_chave.get(k) or self.veic_canon.get(k) or ' '.join(str(nome).split())

    def cliente(self, nome):
        k = chave(nome)
        return self.cli_por_chave.get(k) or self.cli_canon.get(k) or ' '.join(str(nome).split())

    def info_cliente(self, canon):
        return self.cli_info.get(canon, {'diretor_email': None, 'ativo': True, 'grupo': None, 'grupo_negociacao': None})

    def veiculo_conhecido(self, nome):
        k = chave(nome)
        return k == 'livre' or k in self.veic_por_chave or k in self.veic_canon


# ---------------- contexto de um ano ----------------

class Ano:
    """Carrega tudo de um ano e responde as contas."""

    def __init__(self, conn, ano):
        self.conn = conn
        self.ano = int(ano)
        self.fechado = mes_fechado(conn, self.ano)
        self.dp = Depara(conn)
        self.pi = defaultdict(zeros)      # (cli, vei, tipo) -> 12 meses, PIs
        self.proj = defaultdict(zeros)    # (cli, vei, tipo) -> 12 meses, projeção
        self.proj_origem = {}
        for r in conn.execute('SELECT cliente_bruto c, veiculo_bruto v, tipo, mes, SUM(valor) s '
                              'FROM pi WHERE ano=? GROUP BY 1,2,3,4', (self.ano,)):
            k = (self.dp.cliente(r['c']), self.dp.veiculo(r['v']), r['tipo'])
            self.pi[k][r['mes'] - 1] += r['s']
        for r in conn.execute('SELECT * FROM projecao WHERE ano=?', (self.ano,)):
            k = (self.dp.cliente(r['cliente']), self.dp.veiculo(r['veiculo']), r['tipo'])
            self.proj[k][r['mes'] - 1] += r['valor']
            if r['origem']:
                self.proj_origem.setdefault(k, set()).add(r['origem'])
        self.expectativa = defaultdict(int)
        for r in conn.execute('SELECT cliente, valor FROM expectativa WHERE ano=?', (self.ano,)):
            self.expectativa[self.dp.cliente(r['cliente'])] += r['valor']
        self.metas = defaultdict(int)
        for r in conn.execute('SELECT veiculo, valor FROM meta WHERE ano=?', (self.ano,)):
            self.metas[self.dp.veiculo(r['veiculo'])] += r['valor']
        nucleo = json.loads(get_config(conn, 'long_tail_nucleo', '[]'))
        self.nucleo_long_tail = {chave(n) for n in nucleo}
        self.cliente_long_tail = chave(get_config(conn, 'long_tail_cliente', ''))

    # --- valores exibidos: fato até o mês fechado, aposta depois ---

    def mostrado(self, k):
        f = self.fechado
        pi, pr = self.pi.get(k), self.proj.get(k)
        return [(pi[m] if pi else 0) if m < f else (pr[m] if pr else 0) for m in range(12)]

    def aberto(self, k):
        """PIs já emitidos em meses ainda não fechados (linha âmbar)."""
        f = self.fechado
        pi = self.pi.get(k)
        return [pi[m] if pi and m >= f else 0 for m in range(12)]

    def chaves(self):
        return set(self.pi) | set(self.proj)

    def clientes(self):
        """Todos os clientes com algum dado no ano, mais os ativos cadastrados."""
        s = {c for c, _, _ in self.chaves()} | set(self.expectativa)
        s |= {c for c, i in self.dp.cli_info.items() if i['ativo']}
        return sorted(s, key=chave)

    def em_totais(self, cli, incluir_grupos=False):
        return incluir_grupos or not self.dp.info_cliente(cli)['grupo']

    # --- grade ---

    def grade(self, por='veiculo', cliente=None, veiculo=None, clientes=None):
        """Linhas da grade. por='veiculo' (linhas = veículos) ou 'cliente'.
        Retorna {nome: {'FAT': [12], 'REAP': [12], 'aberto_FAT': [12], 'aberto_REAP': [12]}}."""
        out = {}
        for k in self.chaves():
            c, v, t = k
            if cliente is not None and c != cliente:
                continue
            if veiculo is not None and v != veiculo:
                continue
            if clientes is not None and c not in clientes:
                continue
            nome = v if por == 'veiculo' else c
            linha = out.setdefault(nome, {'FAT': zeros(), 'REAP': zeros(), 'aberto_FAT': zeros(), 'aberto_REAP': zeros()})
            for m, x in enumerate(self.mostrado(k)):
                linha[t][m] += x
            for m, x in enumerate(self.aberto(k)):
                linha['aberto_' + t][m] += x
        return out

    def partes(self, meses):
        f = self.fechado
        r = sum(meses[:f])
        p = sum(meses[f:])
        return {'realizado': r, 'projetado': p, 'ano': r + p}

    # --- resumo de cliente ---

    def resumo_cliente(self, cli, filtro_veiculo=None):
        """filtro_veiculo: função(nome_veiculo) -> bool, para recortes (long tail)."""
        f = self.fechado
        res = {'cliente': cli, 'realizado_fat': 0, 'projetado_fat': 0, 'livre': 0,
               'reap_realizado': 0, 'reap_projetado': 0, 'aberto_fat': 0, 'aberto_reap': 0,
               'livre_origem': []}
        for k in self.chaves():
            c, v, t = k
            if c != cli or (filtro_veiculo and not filtro_veiculo(v)):
                continue
            mostr = self.mostrado(k)
            ab = sum(self.aberto(k))
            if t == 'FAT':
                res['realizado_fat'] += sum(mostr[:f])
                res['projetado_fat'] += sum(mostr[f:])
                res['aberto_fat'] += ab
                if v == LIVRE:
                    res['livre'] += sum(mostr[f:])
                    res['livre_origem'] = sorted(self.proj_origem.get(k, ()))
            else:
                res['reap_realizado'] += sum(mostr[:f])
                res['reap_projetado'] += sum(mostr[f:])
                res['aberto_reap'] += ab
        res['expectativa_atual'] = res['realizado_fat'] + res['projetado_fat']
        res['reap'] = res['reap_realizado'] + res['reap_projetado']
        res['expectativa_inicial'] = self.expectativa.get(cli, 0) if not filtro_veiculo else None
        if res['expectativa_inicial'] is not None:
            res['diferenca'] = res['expectativa_atual'] - res['expectativa_inicial']
        info = self.dp.info_cliente(cli)
        res.update({'diretor_email': info['diretor_email'], 'grupo': info['grupo'], 'ativo': info['ativo']})
        return res

    def tem_long_tail(self, cli):
        return bool(self.cliente_long_tail) and chave(cli) == self.cliente_long_tail

    def eh_long_tail(self, veiculo):
        return veiculo != LIVRE and chave(veiculo) not in self.nucleo_long_tail

    # --- consolidado ---

    def consolidado(self, clientes=None, incluir_grupos=False):
        """Resumo por cliente + total. Total = soma dos clientes (nunca agrega antes)."""
        lista = clientes if clientes is not None else self.clientes()
        linhas = [self.resumo_cliente(c) for c in lista]
        dentro = [l for l in linhas if self.em_totais(l['cliente'], incluir_grupos)]
        campos = ['realizado_fat', 'projetado_fat', 'expectativa_atual', 'livre', 'reap_realizado',
                  'reap_projetado', 'reap', 'expectativa_inicial', 'diferenca', 'aberto_fat', 'aberto_reap']
        total = {c: sum(l[c] or 0 for l in dentro) for c in campos}
        for l in linhas:
            l['em_totais'] = self.em_totais(l['cliente'], incluir_grupos)
        return {'clientes': linhas, 'total': total}

    def comparacao_meta(self, linhas_grade):
        """Meta compara só faturável (independe do botão de reap). LIVRE fora dos dois lados."""
        out = {}
        for v, l in linhas_grade.items():
            if v == LIVRE:
                continue
            meta = self.metas.get(v)
            if meta:
                fat = sum(l['FAT'])
                out[v] = {'meta': meta, 'fat': fat, 'pct': fat / meta if meta else None}
        tot_meta = sum(x['meta'] for x in out.values())
        tot_fat = sum(x['fat'] for x in out.values())
        return {'por_veiculo': out, 'total': {'meta': tot_meta, 'fat': tot_fat,
                                              'pct': tot_fat / tot_meta if tot_meta else None}}


def montar_grade(a, linhas, metas=False):
    """Converte linhas de Ano.grade em JSON para a tela: com partes e LIVRE separado."""
    itens = []
    for nome, l in linhas.items():
        item = {'nome': nome, 'livre': nome == LIVRE,
                'fat': l['FAT'], 'reap': l['REAP'],
                'aberto_fat': l['aberto_FAT'], 'aberto_reap': l['aberto_REAP'],
                'fat_partes': a.partes(l['FAT']), 'reap_partes': a.partes(l['REAP'])}
        itens.append(item)
    itens.sort(key=lambda x: (-x['fat_partes']['ano'], chave(x['nome'])))
    total = {'fat': zeros(), 'reap': zeros()}
    for it in itens:
        for m in range(12):
            total['fat'][m] += it['fat'][m]
            total['reap'][m] += it['reap'][m]
    total['fat_partes'] = a.partes(total['fat'])
    total['reap_partes'] = a.partes(total['reap'])
    out = {'linhas': itens, 'total': total, 'fechado': a.fechado}
    if metas:
        cm = a.comparacao_meta(linhas)
        out['metas'] = cm['por_veiculo']
        out['metas_total'] = cm['total']
    return out


def soma_linhas(linhas, filtro):
    """Soma de linhas da grade cujo nome passa no filtro (ex.: long tail)."""
    out = {'FAT': zeros(), 'REAP': zeros(), 'aberto_FAT': zeros(), 'aberto_REAP': zeros()}
    for nome, l in linhas.items():
        if filtro(nome):
            for t in out:
                for m in range(12):
                    out[t][m] += l[t][m]
    return out


def eh_reservado(nome):
    return eh_livre(nome)
