# UnirPix

## Executar os jogos com SQLite (desenvolvimento)

Esta é a versão educacional isolada. Ela usa SQLite, inicia somente em `127.0.0.1` e cria o banco e as sessões em uma pasta temporária, removida quando o servidor é encerrado. Não importa nem altera os JSON antigos.

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python app_sqlite_dev.py
```

Abra `http://127.0.0.1:5051`, crie uma conta fictícia e use o menu **Apostas** para escolher **Roleta numérica** ou **Gatinhos giratórios**. Use o depósito fictício para carregar saldo de teste. Se a porta 5051 já estiver ocupada, escolha outra porta:

```sh
UNIRPIX_DEV_PORT=5052 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python app_sqlite_dev.py
```

Nesse caso, acesse `http://127.0.0.1:5052`.

## Aplicação Flask legada com persistência JSON

O aplicativo exige uma chave secreta definida fora do código. Gere uma chave localmente e exporte-a antes de iniciar:

```sh
export UNIRPIX_SECRET_KEY="$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(48))')"
.venv/bin/python app.py
```

Não compartilhe nem registre o valor da chave. Mantenha a mesma chave entre os reinícios e processos do aplicativo; trocá-la invalida as sessões existentes. O aplicativo falha ao iniciar se a variável estiver ausente ou tiver menos de 32 caracteres.

### Variáveis de ambiente

| Variável | Obrigatória | Uso |
| --- | --- | --- |
| `UNIRPIX_SECRET_KEY` | Sim | Assina tokens CSRF e protege a configuração de sessão. Deve ser aleatória, privada e ter pelo menos 32 caracteres. |
| `UNIRPIX_COOKIE_SECURE` | Em produção HTTPS | Use `1` quando o site estiver servido exclusivamente por HTTPS. Para desenvolvimento local em HTTP, o padrão é `0`. |
| `UNIRPIX_SESSION_DIR` | Não | Diretório dos dados de sessão server-side. O padrão é `instance/sessions`; restrinja o acesso a essa pasta. |
| `UNIRPIX_ENABLE_LEGACY_ACTIVATION` | Apenas para ativação administrativa | Defina como `1` somente ao executar `ativar_conta_antiga.py` localmente. Não habilita uma rota web. |

As sessões usam CacheLib `FileSystemCache` para o ambiente local atual. O cookie contém somente um identificador de sessão; os dados da sessão ficam no diretório server-side. Esse backend é adequado para desenvolvimento em uma única instalação. Para várias instâncias ou produção, configure um backend compartilhado como Redis.

## Ativar senha de uma conta antiga

Contas cadastradas antes da autenticação não têm hash de senha. Uma pessoa administradora com acesso ao sistema de arquivos pode ativar uma delas pelo terminal:

```sh
UNIRPIX_ENABLE_LEGACY_ACTIVATION=1 .venv/bin/python ativar_conta_antiga.py
```

O comando solicita o CPF e a senha sem exibir a senha na tela. Ele só adiciona `senha_hash` a uma conta existente que ainda não tenha hash; não altera saldo ou histórico. Não existe ativação pública por CPF.

Novas senhas precisam ter pelo menos 12 caracteres. O cadastro e a ativação usam o hash seguro do Werkzeug; senhas em texto puro não são salvas.

## Caça-níqueis de gatos — regras acadêmicas

O caça-níqueis é um jogo separado da roleta numérica de 0 a 10. Cada um dos três rolos recebe um símbolo entre 11 gatos, com escolhas independentes e uniformes feitas no servidor. Há 11 × 11 × 11 = 1.331 resultados possíveis; os 11 trios iguais vencem. A probabilidade de vitória é, portanto, 11 / 1.331 = 1 / 121 (aproximadamente 0,8264%).

Cada aposta deve ser positiva, ter no máximo duas casas decimais e respeitar o limite de R$ 1.000.000.000,00 por operação e por saldo. Em uma vitória, o sistema credita 120 vezes o valor apostado como lucro líquido, sem débito prévio. Em uma derrota, debita somente o valor apostado. O crédito máximo da vitória e a capacidade de cobrir uma derrota são verificados antes de gerar os símbolos. Com essas probabilidades e pagamentos, o valor esperado da variação de saldo é zero: (1/121 × 120) − (120/121 × 1) = 0. O dinheiro é exclusivamente fictício.
