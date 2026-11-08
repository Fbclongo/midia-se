# Mídia SW

Controle de investimento em mídia OOH da Streetwise: realizado (PIs) × projeção, por cliente, veículo e mês.
A especificação está em [docs/HANDOFF.md](docs/HANDOFF.md).

## Rodar

Só precisa do Python 3.10+ (sem instalar nenhum pacote).

```
python run.py
```

Abra http://localhost:8000. Em localhost aparece um login de desenvolvimento (escolher a pessoa da equipe).

Outros comandos:

```
python run.py seed     # recria o banco a partir do .xlsx em seed/  (APAGA o banco atual)
python run.py teste    # testes das regras + conferência independente sobre os dados reais
```

O banco fica em `dados/midia.db` (SQLite). Para backup, basta copiar esse arquivo.

## Login Google (produção)

1. No Google Cloud Console, crie um "OAuth client ID" do tipo *Web application*, com a origem do site (ex.: `https://midia.streetwise.com.br`).
2. Crie `config.json` na raiz:

```json
{ "google_client_id": "xxxx.apps.googleusercontent.com", "dominio": "streetwise.com.br", "login_dev": "0", "porta": 8000 }
```

Com `login_dev` = "0" o servidor escuta em todas as interfaces e só aceita login Google de contas @streetwise.com.br que estejam ativas na Equipe.

## Organização

| Arquivo | O quê |
|---|---|
| `midia/calc.py` | **Todas** as regras de cálculo. Telas, chat e Excel usam só ele. |
| `midia/auth.py` | Papéis e permissões (superadmin herda admin por construção). |
| `midia/api.py` | Rotas; toda checagem de permissão é aqui, no servidor. |
| `midia/importacao.py` | Upload de PIs e seed da planilha antiga. |
| `midia/chat.py` | Perguntas sobre os dados, sem IA. |
| `midia/fusoes.py` | Sugestão de fusões do de-para. |
| `midia/db.py` | Esquema SQLite, log e desfazer (snapshots). |
| `web/` | Interface (HTML/CSS/JS puros). |
| `tests/` | Um teste por erro real do handoff + conferência independente. |

Valores são guardados em centavos inteiros; nomes são comparados sem maiúsculas/minúsculas e sem acento.
