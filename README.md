# UnirPix

## Executar com autenticação

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
