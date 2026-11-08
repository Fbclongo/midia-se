"""Servidor HTTP (biblioteca padrão). Sem dependências externas.

Configuração por variáveis de ambiente (ou arquivo config.json na raiz):
  MIDIA_PORTA            porta (padrão 8000)
  MIDIA_GOOGLE_CLIENT_ID client id OAuth do Google (login Workspace)
  MIDIA_DOMINIO          domínio permitido (padrão streetwise.com.br)
  MIDIA_LOGIN_DEV        1 = permite entrar escolhendo o email, SÓ em localhost (padrão 1)
"""
import json
import mimetypes
import os
import traceback
import urllib.parse
import urllib.request
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

from . import api, auth, db
from .db import Transacao, log

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(RAIZ, 'web')


def _config():
    arq = os.path.join(RAIZ, 'config.json')
    c = json.load(open(arq, encoding='utf-8')) if os.path.exists(arq) else {}
    env = os.environ.get
    return {
        # PORT é definido pelo Render (e outras hospedagens)
        'porta': int(env('MIDIA_PORTA') or env('PORT') or c.get('porta') or 8000),
        # email que vira superadmin se o banco estiver sem nenhum (primeira subida)
        'superadmin_inicial': (env('MIDIA_SUPERADMIN') or c.get('superadmin_inicial') or '').strip().lower(),
        'google_client_id': env('MIDIA_GOOGLE_CLIENT_ID') or c.get('google_client_id') or '',
        'dominio': env('MIDIA_DOMINIO') or c.get('dominio') or 'streetwise.com.br',
        'login_dev': (env('MIDIA_LOGIN_DEV') or str(c.get('login_dev', '1'))) == '1',
        # atrás do Caddy (HTTPS) o servidor escuta só localmente e o cookie vira Secure
        'host': env('MIDIA_HOST') or c.get('host') or '127.0.0.1',
        'https': (env('MIDIA_HTTPS') or str(c.get('https', '0'))) == '1',
    }


CONFIG = _config()


class _Codigos:
    """Códigos de login de uso único, válidos por 2 minutos (só em memória)."""

    def __init__(self):
        import threading
        self._d, self._lock = {}, threading.Lock()

    def criar(self, email):
        import secrets
        import time
        c = secrets.token_urlsafe(24)
        with self._lock:
            agora_s = time.time()
            for k in [k for k, (_, t) in self._d.items() if agora_s - t > 120]:
                del self._d[k]
            self._d[c] = (email, agora_s)
        return c

    def usar(self, c):
        import time
        with self._lock:
            v = self._d.pop(c, None)
        if v and time.time() - v[1] <= 120:
            return v[0]
        return None


CODIGOS = _Codigos()


def verificar_google(credencial):
    """Valida o ID token do Google Identity Services. Retorna o email."""
    url = 'https://oauth2.googleapis.com/tokeninfo?id_token=' + urllib.parse.quote(credencial)
    with urllib.request.urlopen(url, timeout=10) as resp:
        info = json.load(resp)
    if info.get('aud') != CONFIG['google_client_id']:
        raise ValueError('token de outro aplicativo')
    if info.get('email_verified') not in ('true', True):
        raise ValueError('email não verificado')
    email = info['email'].lower()
    if info.get('hd') != CONFIG['dominio'] and not email.endswith('@' + CONFIG['dominio']):
        raise ValueError(f'só contas @{CONFIG["dominio"]}')
    return email


class Handler(BaseHTTPRequestHandler):
    server_version = 'MidiaSW'

    def log_message(self, fmt, *args):
        pass

    def _local(self):
        host = (self.headers.get('Host') or '').split(':')[0]
        return host in ('localhost', '127.0.0.1', '[::1]') and self.client_address[0] in ('127.0.0.1', '::1')

    def _token(self):
        c = SimpleCookie(self.headers.get('Cookie') or '')
        return c['sid'].value if 'sid' in c else None

    def _enviar(self, status, corpo, tipo='application/json; charset=utf-8', extra=None):
        if isinstance(corpo, (dict, list)):
            corpo = json.dumps(corpo, ensure_ascii=False, default=str).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', tipo)
        self.send_header('Content-Length', str(len(corpo)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(corpo)

    def _estatico(self, caminho):
        if caminho in ('', '/'):
            caminho = '/index.html'
        arq = os.path.normpath(os.path.join(WEB, caminho.lstrip('/')))
        if not arq.startswith(WEB) or not os.path.isfile(arq):
            arq = os.path.join(WEB, 'index.html')
        tipo = mimetypes.guess_type(arq)[0] or 'application/octet-stream'
        if tipo.startswith('text/') or tipo.endswith('javascript'):
            tipo += '; charset=utf-8'
        self._enviar(200, open(arq, 'rb').read(), tipo)

    def do_GET(self):
        self._tratar('GET')

    def do_POST(self):
        self._tratar('POST')

    def _tratar(self, metodo):
        url = urllib.parse.urlsplit(self.path)
        if url.path == '/login/google' and metodo == 'POST':
            return self._login_google_redirect()
        if not url.path.startswith('/api/'):
            return self._estatico(url.path)
        query = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
        n = int(self.headers.get('Content-Length') or 0)
        raw = self.rfile.read(n) if n else b''
        body = {}
        if raw and (self.headers.get('Content-Type') or '').startswith('application/json'):
            try:
                body = json.loads(raw.decode('utf-8'))
            except ValueError:
                return self._enviar(400, {'erro': 'JSON inválido'})
        conn = db.conectar()
        try:
            if url.path == '/api/dev-pessoas' and metodo == 'GET':
                if not (CONFIG['login_dev'] and self._local()):
                    return self._enviar(403, {'erro': 'só em localhost'})
                return self._enviar(200, [dict(x) for x in conn.execute('SELECT email, nome, papel FROM pessoa WHERE ativo=1 ORDER BY papel, nome')])
            if url.path == '/api/login-dev' and metodo == 'POST':
                return self._login_dev(conn, body)
            if url.path == '/api/login-codigo' and metodo == 'POST':
                return self._login_codigo(conn, body)
            if url.path == '/api/login-google' and metodo == 'POST':
                return self._login_google(conn, body)
            if url.path == '/api/logout' and metodo == 'POST':
                conn.execute('DELETE FROM sessao WHERE token=?', (self._token(),))
                return self._enviar(200, {'ok': True}, extra={'Set-Cookie': 'sid=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax'})
            alvo = api.ROTAS.get((metodo, url.path))
            if not alvo:
                return self._enviar(404, {'erro': 'rota não encontrada'})
            fn, publica = alvo
            pessoa = api.pessoa_da_sessao(conn, self._token())
            if not pessoa and not publica:
                tok = self._token()
                if os.environ.get('MIDIA_DEBUG'):
                    print('401', url.path, 'cookie=', self.headers.get('Cookie'), 'sec-fetch-site=', self.headers.get('Sec-Fetch-Site'), flush=True)
                if not tok:
                    motivo = 'sem cookie'
                elif conn.execute('SELECT 1 FROM sessao WHERE token=?', (tok,)).fetchone():
                    motivo = 'pessoa inativa'
                else:
                    motivo = 'sessão não encontrada'
                return self._enviar(401, {'erro': 'faça login', 'motivo': motivo})
            if metodo == 'POST' and not publica:
                # proteção simples contra CSRF: exige cabeçalho que formulário de outro site não envia
                if self.headers.get('X-Midia') != '1':
                    return self._enviar(403, {'erro': 'requisição recusada'})
            res = fn(api.Req(conn, pessoa, query, body, self.headers, raw))
            if isinstance(res, tuple):
                dados, tipo, nome = res
                return self._enviar(200, dados, tipo, {'Content-Disposition': f"attachment; filename*=UTF-8''{urllib.parse.quote(nome)}"})
            return self._enviar(200, res)
        except auth.Proibido as e:
            return self._enviar(403, {'erro': str(e)})
        except api.Erro as e:
            return self._enviar(e.status, {'erro': str(e)})
        except ValueError as e:
            return self._enviar(400, {'erro': str(e)})
        except Exception as e:
            traceback.print_exc()
            return self._enviar(500, {'erro': f'erro interno: {e}'})
        finally:
            conn.close()

    def _abrir_sessao(self, conn, email, via):
        p = conn.execute('SELECT * FROM pessoa WHERE lower(email)=lower(?) AND ativo=1', (email,)).fetchone()
        if not p:
            return self._enviar(403, {'erro': f'{email} não está ativo na equipe do Mídia SW'})
        with Transacao(conn):
            tok = api.criar_sessao(conn, p['email'])
            log(conn, p['email'], 'LOGIN', via)
        seguro = '; Secure' if CONFIG['https'] else ''
        self._enviar(200, {'ok': True}, extra={'Set-Cookie': f'sid={tok}; Path=/; Max-Age=2592000; HttpOnly; SameSite=Lax{seguro}'})

    def _login_dev(self, conn, body):
        if not (CONFIG['login_dev'] and self._local()):
            return self._enviar(403, {'erro': 'login de desenvolvimento só em localhost'})
        return self._abrir_sessao(conn, (body.get('email') or '').strip().lower(), 'dev')

    def _redirecionar(self, destino, cookie=None):
        self.send_response(303)
        self.send_header('Location', destino)
        self.send_header('Cache-Control', 'no-store')
        if cookie:
            self.send_header('Set-Cookie', cookie)
        self.send_header('Content-Length', '0')
        self.end_headers()

    def _login_google_redirect(self):
        """Login Google no modo redirecionamento: o Google faz POST aqui com o token
        e o usuário volta para a página inicial (sem janela popup)."""
        n = int(self.headers.get('Content-Length') or 0)
        form = {k: v[0] for k, v in urllib.parse.parse_qs(self.rfile.read(n).decode('utf-8', 'replace')).items()}
        erro = lambda msg: self._redirecionar('/?erro=' + urllib.parse.quote(msg))
        if not CONFIG['google_client_id']:
            return erro('login Google não configurado')
        # proteção dupla do Google (quando o navegador manda o cookie)
        c = SimpleCookie(self.headers.get('Cookie') or '')
        if 'g_csrf_token' in c and c['g_csrf_token'].value != form.get('g_csrf_token'):
            return erro('falha de segurança no login; tente de novo')
        try:
            email = verificar_google(form.get('credential') or '')
        except Exception as e:
            print('login google recusado:', e, flush=True)
            return erro(f'login recusado pelo Google: {e}')
        conn = db.conectar()
        try:
            p = conn.execute('SELECT * FROM pessoa WHERE lower(email)=lower(?) AND ativo=1', (email,)).fetchone()
        finally:
            conn.close()
        if not p:
            print('login sem cadastro:', email, flush=True)
            return erro(f'{email} não está ativo na equipe do Mídia SW. Peça a um admin para cadastrar.')
        # O cookie criado aqui (resposta a um envio vindo do Google, "outro site") o Chrome
        # não manda nas chamadas seguintes. Então esta página só leva um código de uso único,
        # e a própria página do site troca o código pela sessão (/api/login-codigo).
        codigo = CODIGOS.criar(p['email'])
        html = ('<!doctype html><meta charset="utf-8"><title>Entrando...</title>'
                '<p style="font-family:system-ui;padding:40px">Entrando...</p>'
                '<script>fetch("/api/login-codigo",{method:"POST",credentials:"same-origin",'
                'headers:{"Content-Type":"application/json"},body:JSON.stringify({codigo:"' + codigo + '"})})'
                '.then(function(r){return r.json().then(function(j){'
                'location.replace(r.ok?"/":"/?erro="+encodeURIComponent(j.erro||"falha no login"))})})'
                '.catch(function(){location.replace("/?erro=falha%20no%20login")})</script>').encode('utf-8')
        self._enviar(200, html, 'text/html; charset=utf-8')

    def _login_codigo(self, conn, body):
        email = CODIGOS.usar(str(body.get('codigo') or ''))
        if not email:
            return self._enviar(403, {'erro': 'código de login expirado; entre de novo'})
        return self._abrir_sessao(conn, email, 'google')

    def _login_google(self, conn, body):
        if not CONFIG['google_client_id']:
            return self._enviar(400, {'erro': 'login Google não configurado'})
        try:
            email = verificar_google(body.get('credential') or '')
        except Exception as e:
            return self._enviar(403, {'erro': f'login recusado: {e}'})
        return self._abrir_sessao(conn, email, 'google')


def preparar_banco():
    """Primeira subida com banco vazio: cria o superadmin inicial (se configurado)."""
    conn = db.conectar()
    try:
        email = CONFIG['superadmin_inicial']
        tem = conn.execute("SELECT COUNT(*) FROM pessoa WHERE papel='superadmin' AND ativo=1").fetchone()[0]
        if email and not tem:
            with Transacao(conn):
                conn.execute("INSERT OR REPLACE INTO pessoa (email, nome, papel, ativo) VALUES (?,?,'superadmin',1)",
                             (email, email.split('@')[0]))
                log(conn, 'sistema', 'SUPERADMIN_INICIAL', email)
            print(f'superadmin inicial criado: {email}', flush=True)
    finally:
        conn.close()


def backup_automatico():
    """Um backup por dia (últimos 30), feito pelo próprio servidor."""
    import datetime
    import threading
    import time

    def laco():
        while True:
            try:
                pasta = os.path.join(os.path.dirname(db.CAMINHO), 'backups')
                hoje = os.path.join(pasta, f'midia-{datetime.date.today().isoformat()}.db')
                if not os.path.exists(hoje):
                    db.backup_diario(pasta)
                    print('backup diário gravado', flush=True)
            except Exception as e:
                print('falha no backup diário:', e, flush=True)
            time.sleep(3600)
    threading.Thread(target=laco, daemon=True).start()


def servir():
    db.conectar().close()
    preparar_banco()
    backup_automatico()
    porta = CONFIG['porta']
    host = '127.0.0.1' if CONFIG['login_dev'] else CONFIG['host']
    srv = ThreadingHTTPServer((host, porta), Handler)
    print(f'Mídia SW rodando em http://{host}:{porta}  login_dev={CONFIG["login_dev"]}  google={"sim" if CONFIG["google_client_id"] else "não"}', flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
