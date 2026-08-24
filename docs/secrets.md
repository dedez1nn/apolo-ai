# Senhas (Bridge e contas IMAP): `pass`/GPG com chave dedicada

`apolo/secrets.py` guarda a senha do Proton Bridge e das senhas de contas IMAP
genéricas fora do `.env` — usando o [`pass`](https://www.passwordstore.org/)
(o "standard unix password manager", baseado em GPG).

## Por que não fica tudo no `.env`

A senha do Bridge **troca a cada sessão dele**, e guardar credencial rotativa
em texto puro num arquivo é frágil. `pass` mantém o segredo criptografado em
disco (`~/.password-store/`), decriptado sob demanda via GPG.

## Por que `pass`/GPG e não o Secret Service do SO (`secret-tool`/gnome-keyring)

Era essa a escolha original — mas o `org.freedesktop.secrets` (D-Bus) **foi
desativado no sistema todo** em 2026-07-16 para destravar um bug do Proton
Mail Bridge (o diálogo gráfico do gnome-keyring nunca renderiza nesta sessão
Hyprland e travava o Bridge por 3 minutos toda vez que abria). Detalhes
completos da causa raiz e da decisão em
`~/dotfiles/troubleshooting/proton-bridge-nao-abre.md` e
`~/dotfiles/troubleshooting/proton-api-keyring-migracao.md` (fora deste repo,
porque é uma configuração do sistema operacional, não do projeto).

Com o Secret Service fora, `secret-tool` deixou de funcionar, e o `apolo` foi
migrado pra `pass`.

## Por que uma chave GPG **dedicada e sem senha**, não a pessoal

`apolo run` roda via **timer do systemd --user** — sem TTY, sem humano pra
responder prompt de senha algum. A chave GPG pessoal do dono tem passphrase
(por design, protege o cofre de verdade); usá-la aqui faria o timer travar
esperando `pinentry` toda vez que o cache do `gpg-agent` expirasse (~10 min
por padrão).

A solução foi criar uma chave **só para essa automação**, sem passphrase
(`%no-protection`), isolada numa subpasta própria do password-store:

```bash
# chave (uma vez só; já feita neste sistema)
gpg --batch --gen-key <<'EOF'
%no-protection
Key-Type: eddsa
Key-Curve: ed25519
Key-Usage: sign
Subkey-Type: ecdh
Subkey-Curve: cv25519
Subkey-Usage: encrypt
Name-Real: Apolo Automation
Name-Email: apolo-automation@localhost
Expire-Date: 0
%commit
EOF

# subpasta com .gpg-id próprio, isolada do resto do password-store
mkdir -p ~/.password-store/apolo
echo "<fingerprint da chave acima>" > ~/.password-store/apolo/.gpg-id
```

Fingerprint atual desta máquina: `FFB7DD86C6172FF341EA6BFBE29B9119DF6E8364`
(`gpg --list-secret-keys apolo-automation@localhost` pra conferir).

Comprometer essa chave não expõe o resto do cofre pessoal — ela só decripta
o que está sob `apolo/`.

## Onde cada segredo mora

| Segredo | Entrada no `pass` |
| --- | --- |
| Senha do Bridge | `apolo/bridge-password` |
| Senha de conta IMAP genérica | `apolo/imap-account/<account_id>` (ex.: `imap:outlook`) |

## API (`apolo/secrets.py`)

```python
disponivel() -> bool                                  # pass no PATH + apolo/.gpg-id existe
store_password(value) -> bool
lookup_password() -> str | None
clear_password() -> bool
store_account_password(account_id, value) -> bool
lookup_account_password(account_id) -> str | None
clear_account_password(account_id) -> bool
```

Se `pass` não estiver instalado, ou `~/.password-store/apolo/.gpg-id` não
existir, `disponivel()` volta `False` e as funções de escrita/leitura
degradam pra `False`/`None` — quem chama cai no fallback do `.env` (username,
não a senha — a senha do Bridge **nunca** vai pro `.env`, fica só no `pass`).

## Verificar se está tudo OK

```bash
cd ~/proton-api
python3 -c "
from apolo import secrets
print('disponivel:', secrets.disponivel())
assert secrets.store_password('teste') and secrets.lookup_password() == 'teste'
assert secrets.clear_password()
print('OK, sem prompt')
"
```

Deve rodar em menos de 1 segundo, sem abrir janela nenhuma. Se `disponivel()`
vier `False`:

1. `which pass` — confirma se o binário existe.
2. `ls ~/.password-store/apolo/.gpg-id` — se sumiu, recrie com o fingerprint
   acima (a chave GPG em si continua existindo em `~/.gnupg`, só o arquivo de
   roteamento do `pass` precisa existir).

## Migração de senhas antigas

Senhas que estavam no Secret Service antigo (antes dessa mudança) **não**
foram migradas automaticamente — ficaram inacessíveis quando o serviço foi
desativado no sistema. Se a senha do Bridge ou de alguma conta IMAP "sumiu",
é só digitar de novo em Settings/IMAP na UI — vai ser salva no novo esquema.
