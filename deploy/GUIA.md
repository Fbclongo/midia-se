# Colocar o Mídia SW no ar (Google Cloud)

Tudo pelo navegador, em console.cloud.google.com, com a conta @streetwise.com.br.

## 1. Projeto
Crie o projeto **midia-sw** e vincule a conta de faturamento da Streetwise.

## 2. Máquina (Compute Engine → Instâncias de VM → Criar)
- Nome: `midia-sw`
- Região: `us-east1` (ou `southamerica-east1` para ficar em São Paulo, um pouco mais caro)
- Tipo: `e2-micro` (dentro do nível gratuito em us-east1) ou `e2-small`
- Disco de inicialização: **Debian 12**, 10 GB, disco padrão
- Firewall: marcar **Permitir tráfego HTTP** e **Permitir tráfego HTTPS**

## 3. IP fixo
Rede VPC → Endereços IP → no IP externo da `midia-sw`, **Promover a endereço IP estático**.
O endereço do sistema será `https://<ip-com-traços>.sslip.io` (ex.: IP 34.73.10.20 → `https://34-73-10-20.sslip.io`).
Isso dispensa mexer no DNS. Para usar `midia.streetwise.com.br` depois: crie um registro A para o IP e rode o instalador com esse domínio.

## 4. Login Google
APIs e serviços → **Tela de permissão OAuth**: tipo **Interno**, nome "Mídia SW", email de suporte.
APIs e serviços → Credenciais → **Criar credenciais → ID do cliente OAuth** → Aplicativo da Web →
Origens JavaScript autorizadas: o endereço do passo 3 (com https://). Copie o **ID do cliente**.

## 5. Instalar
No computador: `python run.py pacote` gera `midia-sw-servidor.zip` (código + banco atual).
Na VM, botão **SSH** → engrenagem/“Fazer upload do arquivo” → envie o zip. Depois:

```
python3 -m zipfile -e midia-sw-servidor.zip . && sudo bash midia-sw/deploy/instalar.sh auto ID_DO_CLIENTE
```

## 6. Backup
- O servidor grava um backup por dia às 3h em `/opt/midia-sw/dados/backups` (últimos 30).
- Superadmin baixa um backup completo em Administração → Desfazer.
- Recomendado: Compute Engine → Snapshots → **Criar programação de snapshot** diária e anexar ao disco `midia-sw`.

## Atualizar o sistema depois
No computador: `python run.py pacote --sem-banco`. Envie o zip pela janela SSH e rode o mesmo comando do passo 5
(sem o ID, ele mantém o anterior). **O banco do servidor nunca é sobrescrito.**

## Comandos úteis na VM
```
sudo systemctl status midia-sw          # está rodando?
sudo journalctl -u midia-sw -n 50       # últimos registros
sudo systemctl restart midia-sw         # reiniciar
```
