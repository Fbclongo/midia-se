"""Conferência independente sobre os dados reais.

Recalcula, direto do .xlsx do seed e sem usar midia.calc, os números por
cliente e compara com o que o sistema mostra. Escrita de outro jeito de
propósito: se as duas divergirem, uma está errada.
"""
import csv
import glob
import io
import os
import unittest
import unicodedata
from decimal import Decimal

from midia import db, calc, importacao, xlsx

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARQ = (glob.glob(os.path.join(RAIZ, 'seed', '*.xlsx')) or [None])[0]
FECHADO = 7


def k(s):
    s = unicodedata.normalize('NFD', str(s))
    return ' '.join(''.join(c for c in s if unicodedata.category(c) != 'Mn').casefold().split())


@unittest.skipUnless(ARQ, 'sem planilha em seed/')
class TestConferencia(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw = open(ARQ, 'rb').read()
        cls.conn = db.conectar(':memory:')
        importacao.seed(cls.conn, raw)
        cls.abas = {k(n): l for n, l in xlsx.ler(raw).items()}

    def esperado(self):
        vei = {k(l[0]): k(l[1]) for l in self.abas['veiculos'][1:] if l and l[0]}
        canon_v = set(vei.values())
        cli = {k(l[0]): k(l[1]) for l in self.abas['clientes'][1:] if l and l[0]}
        grupo = {k(l[1]): (l[4] if len(l) > 4 else '') for l in self.abas['clientes'][1:] if l and l[0]}
        real = {}
        for l in self.abas['pis'][1:]:
            t = k(l[3])
            if t != 'veiculado':
                continue
            serial = float(l[5])
            import datetime
            d = datetime.date(1899, 12, 30) + datetime.timedelta(days=int(serial))
            if d.year != 2026 or d.month > FECHADO:
                continue
            c = cli.get(k(l[1]), k(l[1]))
            real[c] = real.get(c, Decimal(0)) + Decimal(l[4])
        projetado, livre = {}, {}
        for l in self.abas['projecao'][1:]:
            if k(l[2]) != 'fat' or int(float(l[4])) <= FECHADO:
                continue
            c = cli.get(k(l[0]), k(l[0]))
            projetado[c] = projetado.get(c, Decimal(0)) + Decimal(l[5])
            if k(l[1]) == 'livre':
                livre[c] = livre.get(c, Decimal(0)) + Decimal(l[5])
        return real, projetado, livre, grupo

    def test_por_cliente(self):
        real, projetado, livre, grupo = self.esperado()
        a = calc.Ano(self.conn, 2026)
        cons = a.consolidado(incluir_grupos=True)
        for linha in cons['clientes']:
            c = k(linha['cliente'])
            self.assertEqual(linha['realizado_fat'], int(real.get(c, 0) * 100), f'realizado {c}')
            self.assertEqual(linha['projetado_fat'], int(projetado.get(c, 0) * 100), f'projetado {c}')
            self.assertEqual(linha['livre'], int(livre.get(c, 0) * 100), f'livre {c}')
        self.assertEqual(cons['total']['realizado_fat'], int(sum(real.values()) * 100))

    def test_totais_da_migracao(self):
        r = self.conn.execute('SELECT SUM(valor) FROM projecao').fetchone()[0]
        self.assertEqual(r, 10657890723)   # total atual da aba Projecao
        r = self.conn.execute("SELECT SUM(valor) FROM pi WHERE tipo='FAT'").fetchone()[0]
        self.assertEqual(r, 15839719105)

    def test_detector_de_fusoes_reencontra_o_de_para_manual(self):
        from midia import fusoes
        from midia.util import chave
        linhas = [dict(r) for r in self.conn.execute('SELECT nome_bruto, canonico FROM veiculo')]
        canon = {l['nome_bruto']: chave(l['canonico']) for l in linhas}
        grupos = {}
        for l in linhas:
            grupos.setdefault(chave(l['canonico']), set()).add(l['nome_bruto'])
        grupos = {k for k, v in grupos.items() if len(v) > 1}
        pares = fusoes.sugerir([{'nome_bruto': l['nome_bruto'], 'canonico': l['nome_bruto']} for l in linhas])
        achados = {canon[p['a']] for p in pares if canon[p['a']] == canon[p['b']]}
        falsos = [p for p in pares if canon[p['a']] != canon[p['b']]]
        # BrMalls (Helloo, Midia Malls) é subsidiária: não se descobre por algoritmo
        self.assertGreaterEqual(len(achados & grupos), len(grupos) - 1)
        self.assertLessEqual(len(falsos), 1)

    def test_grade_bate_com_resumo(self):
        a = calc.Ano(self.conn, 2026)
        for c in a.clientes():
            g = a.grade(cliente=c)
            fat = sum(sum(l['FAT']) for l in g.values())
            self.assertEqual(fat, a.resumo_cliente(c)['expectativa_atual'], c)
