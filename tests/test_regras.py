"""Um teste para cada erro real descrito no handoff."""
import datetime
import json
import unittest

from midia import db, calc, auth, importacao
from midia.util import centavos, mes_ano, chave, tipo_pi, LIVRE


def banco():
    conn = db.conectar(':memory:')
    conn.executescript("""
      INSERT INTO veiculo (nome_bruto, chave, canonico) VALUES
        ('ELETROMIDIA SA','eletromidia sa','Eletromidia'),
        ('OTIMA','otima','Eletromidia'),
        ('NOS MIDIA','nos midia','Nós'),
        ('ULTRAMIDIA','ultramidia','Ultramidia');
      INSERT INTO cliente (nome_bruto, chave, canonico, diretor_email, grupo) VALUES
        ('Keeta','keeta','Keeta','dir@sw.com',NULL),
        ('Tim','tim','Tim','outro@sw.com','whitelabel'),
        ('Seara','seara','Seara','dir@sw.com',NULL);
    """)
    db.set_config(conn, 'mes_fechado:2026', 7)
    return conn


def pi(conn, cli, vei, tipo, mes, reais, ano=2026):
    conn.execute('INSERT INTO pi (ano, mes, numero, cliente_bruto, veiculo_bruto, tipo, valor) VALUES (?,?,?,?,?,?,?)',
                 (ano, mes, '1', cli, vei, tipo, centavos(reais)))


def proj(conn, cli, vei, tipo, mes, reais, ano=2026):
    conn.execute('INSERT INTO projecao (cliente, veiculo, tipo, ano, mes, valor) VALUES (?,?,?,?,?,?)',
                 (cli, vei, tipo, ano, mes, centavos(reais)))


class TestUtil(unittest.TestCase):
    def test_centavos_nao_arredonda(self):
        # erro: R$ 28.191,42 virava zero ao regravar o valor exibido
        self.assertEqual(centavos('28.191,42'), 2819142)
        self.assertEqual(centavos('1482322.52'), 148232252)
        self.assertEqual(centavos('1.5E7'), 1500000000)
        self.assertEqual(centavos(0.1 + 0.2), 30)

    def test_mes_como_serial_do_sheets(self):
        # erro: 01/2026 virou data no Sheets
        self.assertEqual(mes_ano('46023'), (2026, 1))
        self.assertEqual(mes_ano('46023.0'), (2026, 1))
        self.assertEqual(mes_ano('01/2026'), (2026, 1))
        self.assertEqual(mes_ano('8/2026'), (2026, 8))

    def test_tipo_sem_caixa_e_acento(self):
        for t in ('Veiculado', 'veiculado', 'VEICULADO'):
            self.assertEqual(tipo_pi(t), 'FAT')
        for t in ('REAPLICAÇÃO', 'Reaplicação', 'reaplicacao'):
            self.assertEqual(tipo_pi(t), 'REAP')
        self.assertIsNone(tipo_pi('Permuta sem comissão'))

    def test_chave(self):
        self.assertEqual(chave('  BRMalls '), chave('BrMalls'))
        self.assertEqual(chave('Nós'), 'nos')


class TestCanonizacao(unittest.TestCase):
    def test_caixa_diferente_cai_no_canonico(self):
        conn = banco()
        pi(conn, 'KEETA', 'eletromidia', 'FAT', 3, 100)   # sem linha no de-para, mas bate com o canônico
        pi(conn, 'Keeta', 'Otima', 'FAT', 3, 50)          # subsidiária mapeada
        a = calc.Ano(conn, 2026)
        g = a.grade(cliente='Keeta')
        self.assertEqual(list(g), ['Eletromidia'])
        self.assertEqual(g['Eletromidia']['FAT'][2], 15000)

    def test_nome_novo_nunca_some(self):
        conn = banco()
        pis = [{'numero': '1', 'cliente_bruto': 'Cliente Novo', 'veiculo_bruto': 'Veiculo Novo', 'tipo': 'FAT',
                'valor': 1000, 'ano': 2026, 'mes': 2}]
        importacao.gravar_pis(conn, 't', pis)
        r = conn.execute("SELECT * FROM veiculo WHERE chave='veiculo novo'").fetchone()
        self.assertEqual((r['status'], r['canonico']), ('pendente', 'Veiculo Novo'))
        a = calc.Ano(conn, 2026)
        self.assertEqual(a.resumo_cliente('Cliente Novo')['realizado_fat'], 1000)

    def test_corrigir_depara_corrige_historico(self):
        conn = banco()
        pi(conn, 'Keeta', 'GLOBO COMUNICACAO', 'FAT', 1, 10)
        conn.execute("INSERT INTO veiculo (nome_bruto, chave, canonico) VALUES ('GLOBO COMUNICACAO','globo comunicacao','Eletromidia')")
        self.assertIn('Eletromidia', calc.Ano(conn, 2026).grade(cliente='Keeta'))


class TestMesFechado(unittest.TestCase):
    def test_realizado_so_em_mes_fechado(self):
        conn = banco()
        pi(conn, 'Keeta', 'Eletromidia', 'FAT', 7, 100)
        pi(conn, 'Keeta', 'Eletromidia', 'FAT', 8, 40)      # PI de agosto já subiu, mas agosto não fechou
        proj(conn, 'Keeta', 'Eletromidia', 'FAT', 7, 999)   # projeção de mês passado: guardada, não exibida
        proj(conn, 'Keeta', 'Eletromidia', 'FAT', 8, 60)
        r = calc.Ano(conn, 2026).resumo_cliente('Keeta')
        self.assertEqual(r['realizado_fat'], 10000)
        self.assertEqual(r['projetado_fat'], 6000)
        self.assertEqual(r['aberto_fat'], 4000)             # linha âmbar
        self.assertEqual(r['expectativa_atual'], 16000)

    def test_primeira_leitura_grava_e_congela_sem_calendario(self):
        # erros: promoveu agosto sozinho no dia 1º; e caía no calendário quando vazio
        conn = banco()
        self.assertEqual(calc.mes_fechado(conn, 2027), 0)
        self.assertEqual(db.get_config(conn, 'mes_fechado:2027'), '0')
        self.assertEqual(calc.mes_fechado(conn, 2026), 7)

    def test_nao_fecha_mes_que_nao_terminou(self):
        hoje = datetime.date(2026, 11, 6)
        self.assertTrue(calc.pode_fechar(2026, 10, hoje))
        self.assertFalse(calc.pode_fechar(2026, 11, hoje))
        self.assertFalse(calc.pode_fechar(2027, 1, hoje))
        self.assertTrue(calc.pode_fechar(2026, 0, hoje))


class TestLivre(unittest.TestCase):
    def test_livre_e_so_a_linha_livre(self):
        conn = banco()
        conn.execute("INSERT INTO expectativa VALUES ('Keeta', 2026, ?, 't', 't')", (centavos(1000),))
        proj(conn, 'Keeta', 'Eletromidia', 'FAT', 9, 100)
        r = calc.Ano(conn, 2026).resumo_cliente('Keeta')
        self.assertEqual(r['livre'], 0)   # sem linha LIVRE, livre é zero mesmo com expectativa alta

    def test_livre_nao_fica_negativo_nem_come_dos_outros(self):
        conn = banco()
        conn.execute("INSERT INTO expectativa VALUES ('Keeta', 2026, ?, 't', 't')", (centavos(10),))
        proj(conn, 'Keeta', 'Eletromidia', 'FAT', 9, 500)       # alocado acima do combinado
        proj(conn, 'Seara', LIVRE, 'FAT', 9, 30)
        proj(conn, 'Seara', 'livre', 'FAT', 10, 20)             # caixa diferente, mesmo LIVRE
        c = calc.Ano(conn, 2026).consolidado()
        self.assertEqual(c['total']['livre'], 5000)

    def test_livre_de_mes_passado_nao_conta(self):
        conn = banco()
        proj(conn, 'Seara', LIVRE, 'FAT', 6, 30)
        self.assertEqual(calc.Ano(conn, 2026).resumo_cliente('Seara')['livre'], 0)


class TestReap(unittest.TestCase):
    def test_reap_fora_das_contas_de_dinheiro(self):
        conn = banco()
        pi(conn, 'Keeta', 'Eletromidia', 'REAP', 3, 1000)
        pi(conn, 'Keeta', 'Eletromidia', 'FAT', 3, 100)
        proj(conn, 'Keeta', LIVRE, 'REAP', 9, 70)
        conn.execute("INSERT INTO meta VALUES ('Eletromidia', 2026, ?, 't', 't')", (centavos(200),))
        a = calc.Ano(conn, 2026)
        r = a.resumo_cliente('Keeta')
        self.assertEqual(r['expectativa_atual'], 10000)
        self.assertEqual(r['livre'], 0)
        self.assertEqual(r['reap'], 107000)
        m = a.comparacao_meta(a.grade())
        self.assertEqual(m['por_veiculo']['Eletromidia']['fat'], 10000)

    def test_meta_ignora_livre_dos_dois_lados(self):
        conn = banco()
        proj(conn, 'Keeta', LIVRE, 'FAT', 9, 70)
        conn.execute("INSERT INTO meta VALUES ('Eletromidia', 2026, ?, 't', 't')", (centavos(200),))
        a = calc.Ano(conn, 2026)
        self.assertEqual(a.comparacao_meta(a.grade())['total']['fat'], 0)


class TestConsolidado(unittest.TestCase):
    def test_grupo_fora_dos_totais_por_padrao(self):
        conn = banco()
        pi(conn, 'Tim', 'Eletromidia', 'FAT', 2, 100)
        pi(conn, 'Keeta', 'Eletromidia', 'FAT', 2, 1)
        a = calc.Ano(conn, 2026)
        self.assertEqual(a.consolidado()['total']['realizado_fat'], 100)
        self.assertEqual(a.consolidado(incluir_grupos=True)['total']['realizado_fat'], 10100)


class TestLongTail(unittest.TestCase):
    def test_long_tail_por_chave(self):
        conn = banco()
        db.set_config(conn, 'long_tail_nucleo', json.dumps(['ELETROMIDIA']))
        a = calc.Ano(conn, 2026)
        self.assertFalse(a.eh_long_tail('Eletromidia'))
        self.assertTrue(a.eh_long_tail('Nós'))
        self.assertFalse(a.eh_long_tail(LIVRE))


class TestPermissoes(unittest.TestCase):
    def p(self, papel, email='x@sw.com', ativo=True):
        return {'email': email, 'papel': papel, 'ativo': ativo}

    def test_superadmin_herda_admin(self):
        # erro: checagens ['admin','operacao'] deixaram o superadmin sem subir PI
        for cap in auth.CAPACIDADES['admin']:
            self.assertTrue(auth.pode(self.p('superadmin'), cap), cap)
        self.assertTrue(auth.pode(self.p('superadmin'), 'pis'))

    def test_matriz(self):
        tab = {
            'visao_geral': ('superadmin', 'admin'), 'pis': ('superadmin', 'admin', 'operacao'),
            'fechar_mes': ('superadmin', 'admin'), 'equipe': ('superadmin', 'admin'),
            'papeis_admin': ('superadmin',), 'meta': ('superadmin',), 'uso': ('superadmin',),
        }
        for cap, quem in tab.items():
            for papel in auth.PAPEIS:
                self.assertEqual(auth.pode(self.p(papel), cap), papel in quem, f'{papel} {cap}')

    def test_inativo_nao_pode_nada(self):
        self.assertFalse(auth.pode(self.p('superadmin', ativo=False), 'pis'))

    def test_diretor_so_os_dele(self):
        d = self.p('diretor', 'dir@sw.com')
        self.assertTrue(auth.pode_ver_cliente(d, {'diretor_email': 'DIR@sw.com'}))
        self.assertFalse(auth.pode_ver_cliente(d, {'diretor_email': 'outro@sw.com'}))
        self.assertFalse(auth.pode_ver_cliente(self.p('operacao'), {'diretor_email': 'x@sw.com'}))

    def test_ultimo_superadmin(self):
        conn = banco()
        conn.execute("INSERT INTO pessoa VALUES ('s@sw.com','S','superadmin',1)")
        s = self.p('superadmin', 's@sw.com')
        with self.assertRaises(ValueError):
            auth.checar_mudanca_papel(conn, s, 's@sw.com', 'admin', True)
        with self.assertRaises(ValueError):
            auth.checar_mudanca_papel(conn, s, 's@sw.com', 'superadmin', False)
        conn.execute("INSERT INTO pessoa VALUES ('t@sw.com','T','superadmin',1)")
        auth.checar_mudanca_papel(conn, s, 's@sw.com', 'admin', True)

    def test_admin_nao_cria_admin(self):
        conn = banco()
        with self.assertRaises(auth.Proibido):
            auth.checar_mudanca_papel(conn, self.p('admin'), 'novo@sw.com', 'admin', True)
        auth.checar_mudanca_papel(conn, self.p('admin'), 'novo@sw.com', 'diretor', True)


class TestDesfazer(unittest.TestCase):
    def test_upload_pode_ser_desfeito(self):
        conn = banco()
        pi(conn, 'Keeta', 'Eletromidia', 'FAT', 1, 100)
        sid, _, _ = importacao.gravar_pis(conn, 't', [{'numero': '9', 'cliente_bruto': 'Keeta', 'veiculo_bruto': 'X',
                                                         'tipo': 'FAT', 'valor': 1, 'ano': 2026, 'mes': 1}])
        db.restaurar(conn, sid, 't')
        self.assertEqual(conn.execute('SELECT SUM(valor) FROM pi').fetchone()[0], 10000)
        self.assertIsNone(conn.execute("SELECT * FROM veiculo WHERE chave='x'").fetchone())

    def test_substituir_meses_preserva_os_outros(self):
        conn = banco()
        pi(conn, 'Keeta', 'Eletromidia', 'FAT', 1, 100)
        pi(conn, 'Keeta', 'Eletromidia', 'FAT', 8, 5)
        importacao.gravar_pis(conn, 't', [{'numero': '9', 'cliente_bruto': 'Keeta', 'veiculo_bruto': 'Eletromidia',
                                           'tipo': 'FAT', 'valor': 700, 'ano': 2026, 'mes': 8}], modo='substituir_meses')
        self.assertEqual(conn.execute('SELECT SUM(valor) FROM pi').fetchone()[0], 10700)


class TestBackupRestauracao(unittest.TestCase):
    def setUp(self):
        import tempfile, os
        self.dir = tempfile.mkdtemp()
        self.antigo = db.CAMINHO
        db.CAMINHO = os.path.join(self.dir, 'midia.db')

    def tearDown(self):
        db.CAMINHO = self.antigo

    def test_restaurar_substitui_e_guarda_o_anterior(self):
        import os
        origem = db.conectar(os.path.join(self.dir, 'origem.db'))
        origem.execute("INSERT INTO pessoa VALUES ('s@sw.com','S','superadmin',1)")
        pi(origem, 'Keeta', 'Eletromidia', 'FAT', 1, 100)
        origem.close()
        atual = db.conectar()
        atual.execute("INSERT INTO sessao VALUES ('tok','s@sw.com','x')")
        atual.close()
        res = db.restaurar_arquivo(open(os.path.join(self.dir, 'origem.db'), 'rb').read(), 's@sw.com')
        self.assertEqual(res['pi'], 1)
        c = db.conectar()
        self.assertEqual(c.execute('SELECT SUM(valor) FROM pi').fetchone()[0], 10000)
        self.assertIsNotNone(c.execute("SELECT 1 FROM sessao WHERE token='tok'").fetchone())   # continua logado
        self.assertTrue(any(n.startswith('antes-de-restaurar') for n in os.listdir(os.path.join(self.dir, 'backups'))))

    def test_nao_restaura_backup_que_tranca_o_autor_fora(self):
        import os
        origem = db.conectar(os.path.join(self.dir, 'origem.db'))
        origem.execute("INSERT INTO pessoa VALUES ('outro@sw.com','O','superadmin',1)")
        origem.close()
        with self.assertRaises(ValueError):
            db.restaurar_arquivo(open(os.path.join(self.dir, 'origem.db'), 'rb').read(), 's@sw.com')
        with self.assertRaises(ValueError):
            db.restaurar_arquivo(b'nao e sqlite', 's@sw.com')


if __name__ == '__main__':
    unittest.main()
