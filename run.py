"""Mídia SW — ponto de entrada.

  python run.py                 sobe o servidor em http://localhost:8000
  python run.py seed [arquivo]  recria o banco a partir da planilha antiga (.xlsx)
  python run.py teste           roda os testes e a conferência independente
"""
import glob
import json
import os
import sys

RAIZ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, RAIZ)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'servir'
    if cmd == 'seed':
        from midia import db, importacao
        arq = sys.argv[2] if len(sys.argv) > 2 else (glob.glob(os.path.join(RAIZ, 'seed', '*.xlsx')) or [None])[0]
        if not arq:
            sys.exit('coloque o .xlsx da planilha antiga na pasta seed/')
        conn = db.conectar()
        with db.Transacao(conn):
            rel = importacao.seed(conn, open(arq, 'rb').read())
        print(json.dumps(rel, ensure_ascii=False, indent=1))
    elif cmd == 'backup':
        from midia import db
        print('backup gravado em', db.backup_diario())
    elif cmd == 'pacote':
        pacote(com_banco='--sem-banco' not in sys.argv)
    elif cmd == 'teste':
        import unittest
        r = unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.discover(os.path.join(RAIZ, 'tests'), top_level_dir=RAIZ))
        sys.exit(0 if r.wasSuccessful() else 1)
    else:
        from midia import server
        server.servir()


def pacote(com_banco=True):
    """Gera midia-sw-servidor.zip para enviar ao servidor (código + banco atual)."""
    import tempfile
    import zipfile
    from midia import db
    destino = os.path.join(RAIZ, 'midia-sw-servidor.zip')
    with zipfile.ZipFile(destino, 'w', zipfile.ZIP_DEFLATED) as z:
        for pasta in ('midia', 'web', 'deploy', 'docs', 'tests'):
            for raiz, _, arqs in os.walk(os.path.join(RAIZ, pasta)):
                if '__pycache__' in raiz:
                    continue
                for a in arqs:
                    p = os.path.join(raiz, a)
                    nome = 'midia-sw/' + os.path.relpath(p, RAIZ).replace(os.sep, '/')
                    if a.endswith('.sh'):   # bash no Linux não aceita fim de linha do Windows
                        z.writestr(nome, open(p, 'rb').read().replace(b'\r\n', b'\n'))
                    else:
                        z.write(p, nome)
        for a in ('run.py', 'README.md'):
            z.write(os.path.join(RAIZ, a), 'midia-sw/' + a)
        if com_banco:
            tmp = os.path.join(tempfile.gettempdir(), 'midia-pacote.db')
            db.backup(tmp)
            z.write(tmp, 'midia-sw/dados/midia.db')
            os.remove(tmp)
    print(f'pacote gerado: {destino} ({os.path.getsize(destino) // 1024} KB, {"com" if com_banco else "sem"} banco)')


if __name__ == '__main__':
    main()
