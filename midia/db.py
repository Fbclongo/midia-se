"""Banco SQLite. Valores sempre em centavos inteiros — nunca arredonda."""
import json
import os
import sqlite3
import threading

from .util import chave, agora

CAMINHO = os.environ.get('MIDIA_DB', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'dados', 'midia.db'))

SCHEMA = """
CREATE TABLE IF NOT EXISTS pi (
  id INTEGER PRIMARY KEY,
  ano INTEGER NOT NULL, mes INTEGER NOT NULL,
  numero TEXT, cliente_bruto TEXT NOT NULL, veiculo_bruto TEXT NOT NULL,
  tipo TEXT NOT NULL CHECK (tipo IN ('FAT','REAP')),
  valor INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS pi_ano ON pi(ano);

-- de-para de veículos: uma linha por grafia (chave = nome sem caixa/acento)
CREATE TABLE IF NOT EXISTS veiculo (
  id INTEGER PRIMARY KEY,
  nome_bruto TEXT NOT NULL, chave TEXT NOT NULL UNIQUE,
  canonico TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'ativo' CHECK (status IN ('ativo','pendente')),
  ativo INTEGER NOT NULL DEFAULT 1
);

-- de-para de clientes. diretor/grupo/ativo valem para o canônico inteiro.
CREATE TABLE IF NOT EXISTS cliente (
  id INTEGER PRIMARY KEY,
  nome_bruto TEXT NOT NULL, chave TEXT NOT NULL UNIQUE,
  canonico TEXT NOT NULL,
  diretor_email TEXT, ativo INTEGER NOT NULL DEFAULT 1,
  grupo TEXT,               -- preenchido = fora dos totais por padrão
  grupo_negociacao TEXT,    -- para somar (ex.: ZAMP). Ainda sem uso nas contas.
  status TEXT NOT NULL DEFAULT 'ativo' CHECK (status IN ('ativo','pendente'))
);

CREATE TABLE IF NOT EXISTS pessoa (
  email TEXT PRIMARY KEY, nome TEXT,
  papel TEXT NOT NULL CHECK (papel IN ('superadmin','admin','diretor','operacao')),
  ativo INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS expectativa (
  cliente TEXT NOT NULL, ano INTEGER NOT NULL, valor INTEGER NOT NULL,
  quem TEXT, quando TEXT, PRIMARY KEY (cliente, ano)
);

-- projeção: meses passados nunca são apagados
CREATE TABLE IF NOT EXISTS projecao (
  cliente TEXT NOT NULL, veiculo TEXT NOT NULL,
  tipo TEXT NOT NULL CHECK (tipo IN ('FAT','REAP')),
  ano INTEGER NOT NULL, mes INTEGER NOT NULL, valor INTEGER NOT NULL,
  quem TEXT, quando TEXT,
  origem TEXT,              -- ex.: 'OTHERS do forecast' na migração
  PRIMARY KEY (cliente, veiculo, tipo, ano, mes)
);

CREATE TABLE IF NOT EXISTS meta (
  veiculo TEXT NOT NULL, ano INTEGER NOT NULL, valor INTEGER NOT NULL,
  quem TEXT, quando TEXT, PRIMARY KEY (veiculo, ano)
);

CREATE TABLE IF NOT EXISTS pacote (
  id TEXT PRIMARY KEY, cliente TEXT NOT NULL, veiculo TEXT, valor INTEGER,
  inicio TEXT, fim TEXT, descricao TEXT, status TEXT, quem TEXT, quando TEXT
);

CREATE TABLE IF NOT EXISTS config (chave TEXT PRIMARY KEY, valor TEXT);

CREATE TABLE IF NOT EXISTS acesso (quando TEXT, email TEXT, nome TEXT, papel TEXT);

CREATE TABLE IF NOT EXISTS log (id INTEGER PRIMARY KEY, quando TEXT, quem TEXT, acao TEXT, detalhe TEXT);

-- desfazer: cópia das linhas afetadas antes de cada operação destrutiva
CREATE TABLE IF NOT EXISTS snapshot (
  id INTEGER PRIMARY KEY, quando TEXT, quem TEXT, descricao TEXT,
  dados TEXT NOT NULL, desfeito INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sessao (token TEXT PRIMARY KEY, email TEXT, criado TEXT);
"""

_lock = threading.RLock()


def conectar(caminho=None):
    caminho = caminho or CAMINHO
    if caminho != ':memory:':
        os.makedirs(os.path.dirname(caminho), exist_ok=True)
    conn = sqlite3.connect(caminho, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.create_function('chave', 1, chave, deterministic=True)
    conn.execute('PRAGMA journal_mode=WAL' if caminho != ':memory:' else 'PRAGMA journal_mode=MEMORY')
    conn.executescript(SCHEMA)
    return conn


class Transacao:
    """with Transacao(conn): ...  — tudo ou nada, serializado."""

    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        _lock.acquire()
        self.conn.execute('BEGIN IMMEDIATE')
        return self.conn

    def __exit__(self, tipo, *_):
        try:
            self.conn.execute('COMMIT' if tipo is None else 'ROLLBACK')
        finally:
            _lock.release()
        return False


def log(conn, quem, acao, detalhe):
    if not isinstance(detalhe, str):
        detalhe = json.dumps(detalhe, ensure_ascii=False)
    conn.execute('INSERT INTO log (quando, quem, acao, detalhe) VALUES (?,?,?,?)', (agora(), quem, acao, detalhe))


def get_config(conn, k, padrao=None):
    r = conn.execute('SELECT valor FROM config WHERE chave=?', (k,)).fetchone()
    return r['valor'] if r else padrao


def set_config(conn, k, v):
    conn.execute('INSERT INTO config (chave, valor) VALUES (?,?) ON CONFLICT(chave) DO UPDATE SET valor=excluded.valor', (k, str(v)))


# ---------- backup ----------

def backup(destino, origem=None):
    """Cópia consistente do banco (API de backup do SQLite), mesmo com o servidor rodando."""
    os.makedirs(os.path.dirname(os.path.abspath(destino)), exist_ok=True)
    src = sqlite3.connect(origem or CAMINHO)
    dst = sqlite3.connect(destino)
    try:
        with _lock:
            src.backup(dst)
    finally:
        dst.close()
        src.close()
    return destino


def restaurar_arquivo(conteudo, email_autor):
    """Substitui o banco inteiro pelo conteúdo de um backup .db.
    Guarda antes uma cópia do banco atual em backups/antes-de-restaurar-*.db.
    Exige que o backup tenha as tabelas do sistema e o autor como superadmin ativo,
    para ninguém se trancar fora."""
    import datetime
    import tempfile
    tmp = os.path.join(tempfile.gettempdir(), f'midia-restaurar-{os.getpid()}-{threading.get_ident()}.db')
    with open(tmp, 'wb') as f:
        f.write(conteudo)
    src = sqlite3.connect(tmp)
    try:
        try:
            tabelas = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        except sqlite3.DatabaseError:
            raise ValueError('o arquivo não é um backup do Mídia SW (.db)')
        faltam = {'pi', 'veiculo', 'cliente', 'pessoa', 'projecao'} - tabelas
        if faltam:
            raise ValueError(f'o arquivo não é um backup do Mídia SW (faltam tabelas: {", ".join(sorted(faltam))})')
        ok = src.execute("SELECT 1 FROM pessoa WHERE lower(email)=lower(?) AND papel='superadmin' AND ativo=1",
                         (email_autor,)).fetchone()
        if not ok:
            raise ValueError(f'nesse backup {email_autor} não é superadmin ativo — você ficaria sem acesso')
        resumo = {t: src.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in ('pi', 'projecao', 'pessoa')}
        pasta = os.path.join(os.path.dirname(CAMINHO), 'backups')
        antes = backup(os.path.join(pasta, f'antes-de-restaurar-{datetime.datetime.now():%Y-%m-%d-%H%M%S}.db'))
        dst = sqlite3.connect(CAMINHO)
        sessoes = []
        try:
            with _lock:
                if 'sessao' in {r[0] for r in dst.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
                    sessoes = dst.execute('SELECT * FROM sessao').fetchall()
                src.backup(dst)
                dst.executescript(SCHEMA)
                # mantém quem está logado agora
                dst.executemany('INSERT OR IGNORE INTO sessao VALUES (?,?,?)', sessoes)
                dst.commit()
        finally:
            dst.close()
        return {'antes': os.path.basename(antes), **resumo}
    finally:
        src.close()
        if os.path.exists(tmp):
            os.remove(tmp)


def backup_diario(pasta=None, manter=30):
    """Grava dados/backups/midia-AAAA-MM-DD.db e apaga os mais antigos que `manter`."""
    import datetime
    import glob
    pasta = pasta or os.path.join(os.path.dirname(CAMINHO), 'backups')
    arq = backup(os.path.join(pasta, f'midia-{datetime.date.today().isoformat()}.db'))
    antigos = sorted(glob.glob(os.path.join(pasta, 'midia-*.db')))[:-manter]
    for a in antigos:
        os.remove(a)
    return arq


# ---------- desfazer ----------

def snapshot(conn, quem, descricao, tabelas):
    """tabelas: {nome_tabela: 'clausula WHERE' ou None (tabela inteira), ...}
    Guarda as linhas atuais para poder restaurar. Retorna id."""
    dados = {}
    for tabela, (where, params) in tabelas.items():
        sql = f'SELECT * FROM {tabela}' + (f' WHERE {where}' if where else '')
        dados[tabela] = {'where': where, 'params': list(params),
                         'linhas': [dict(r) for r in conn.execute(sql, params)]}
    cur = conn.execute('INSERT INTO snapshot (quando, quem, descricao, dados) VALUES (?,?,?,?)',
                       (agora(), quem, descricao, json.dumps(dados, ensure_ascii=False)))
    return cur.lastrowid


def restaurar(conn, sid, quem):
    r = conn.execute('SELECT * FROM snapshot WHERE id=?', (sid,)).fetchone()
    if not r:
        raise ValueError('snapshot não encontrado')
    if r['desfeito']:
        raise ValueError('esta operação já foi desfeita')
    dados = json.loads(r['dados'])
    # antes de restaurar, guarda o estado atual (desfazer o desfazer)
    snapshot(conn, quem, f'Antes de desfazer: {r["descricao"]}',
             {t: (d['where'], d['params']) for t, d in dados.items()})
    for tabela, d in dados.items():
        conn.execute(f'DELETE FROM {tabela}' + (f' WHERE {d["where"]}' if d['where'] else ''), d['params'])
        for linha in d['linhas']:
            cols = list(linha)
            conn.execute(f'INSERT INTO {tabela} ({",".join(cols)}) VALUES ({",".join("?" * len(cols))})',
                         [linha[c] for c in cols])
    conn.execute('UPDATE snapshot SET desfeito=1 WHERE id=?', (sid,))
    log(conn, quem, 'DESFAZER', f'#{sid} {r["descricao"]}')
