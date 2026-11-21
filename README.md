# 🐈‍⬛ UnirPix

O **UnirPix** é um projeto acadêmico desenvolvido em Python e Flask. Ele simula uma pequena plataforma financeira para fins de aprendizagem: cadastro, autenticação, saldo, depósitos fictícios, histórico e dois jogos no mesmo aplicativo. **Todos os valores são créditos fictícios; não há dinheiro real nem conversão em dinheiro real.**

## Índice

- [Funcionalidades](#funcionalidades)
- [Roleta numérica](#roleta-numérica)
- [Cat's Coins](#cats-coins)
- [Moeda fictícia](#moeda-fictícia)
- [Tecnologias](#tecnologias)
- [Como executar](#como-executar)
- [Testes](#testes)
- [Aviso sobre o ambiente](#aviso-sobre-o-ambiente)

## Funcionalidades

- Cadastro com nome, CPF e senha protegida por hash.
- Login e logout com sessão armazenada no servidor.
- Proteção CSRF nos formulários.
- Consulta do saldo da conta autenticada.
- Depósitos de teste, sem movimentação de dinheiro real.
- Histórico de depósitos e resultados dos jogos.
- Menu para escolher entre a roleta numérica e o caça-níqueis Cat's Coins.
- Interface web responsiva, com efeitos sonoros opcionais e respeito à preferência de movimento reduzido do navegador.

## Roleta numérica

A roleta acadêmica permite escolher um número inteiro de **0 a 10** e informar o valor fictício da aposta. O servidor valida os dados e sorteia o número. Se o jogador acertar, recebe um crédito de **10 vezes o valor apostado**, sem débito prévio. Se errar, o valor da aposta é debitado do saldo. O resultado e a movimentação são registrados no histórico.

## Cat's Coins

Cat's Coins é o caça-níqueis de três rolos. Cada rolo realiza seu próprio sorteio ponderado **no servidor**. As probabilidades abaixo se aplicam separadamente a cada rolo; os pesos somam 100%.

| Raça | Probabilidade por rolo | Multiplicador |
| --- | ---: | ---: |
| Maine Coon | 25% | 2× |
| Persa | 20% | 3× |
| Siamês | 15% | 5× |
| Bengala | 12% | 8× |
| Ragdoll | 9% | 10× |
| British Shorthair | 7% | 15× |
| Sphynx | 5% | 20× |
| Scottish Fold | 3% | 30× |
| Angorá Turco | 2% | 40× |
| Abissínio | 1,5% | 60× |
| Azul Russo | 0,5% | 100× |

O resultado de cada rodada segue estas regras:

- **Três gatos iguais:** crédito do prêmio completo, calculado uma vez com o multiplicador da raça.
- **Exatamente dois gatos iguais:** crédito de um terço do prêmio completo da raça repetida, arredondado ao centavo mais próximo.
- **Três gatos diferentes:** débito do valor apostado.

Os prêmios são creditados sem débito prévio. Antes de sortear, o sistema valida saldo suficiente para cobrir uma derrota e espaço para o prêmio máximo de 100×. A aposta deve ser positiva, ter no máximo duas casas decimais e respeitar os limites financeiros definidos no código.

As imagens dos símbolos são PNGs transparentes, exibidos na ordem da tabela. A animação dos rolos para nos símbolos que o servidor já sorteou; o navegador não sorteia nem altera o resultado.

## Moeda fictícia

**Cat's Coins** é a unidade de crédito fictícia usada pelo caça-níqueis. O contador mostra o saldo disponível; quando há prêmio, moedas animadas sobem dos rolos até o contador enquanto o valor confirmado pelo servidor é apresentado progressivamente. Os sons são sintetizados no navegador, podem ser desligados e só começam após interação do usuário.

![Moeda metálica Cat's Coins](static/imagens/cats-coins/moeda-original.png)

## Tecnologias

- **Python:** regras de negócio, validações e execução do servidor.
- **Flask:** rotas e renderização das páginas.
- **SQLite:** usuários, contas, saldo e histórico na versão educacional de desenvolvimento.
- **HTML:** estrutura das páginas e formulários.
- **CSS:** identidade visual e adaptação para telas menores.
- **JavaScript:** animações dos rolos e das moedas, atualização visual do saldo e sons opcionais.

## Como executar

Requisitos: **Python 3.10 ou mais recente** e Git.

1. Clone o repositório e entre na pasta:

   ```sh
   git clone https://github.com/ynau4k/unirpix.git
   cd unirpix
   ```

2. Crie e ative um ambiente virtual:

   **macOS ou Linux**

   ```sh
   python3 -m venv .venv
   source .venv/bin/activate
   ```

   **Windows PowerShell**

   ```powershell
   py -m venv .venv
   .venv\Scripts\Activate.ps1
   ```

3. Instale as dependências públicas do projeto:

   ```sh
   python -m pip install -r requirements.txt
   ```

4. Inicie o aplicativo SQLite de desenvolvimento:

   ```sh
   python app_sqlite_dev.py
   ```

5. Abra [http://127.0.0.1:5051](http://127.0.0.1:5051) no navegador, crie uma conta fictícia e use o depósito de teste para adicionar créditos. No menu **Jogos**, escolha **Roleta numérica** ou **Cat's Coins**.

Se a porta padrão estiver ocupada, inicie o servidor em outra porta:

```sh
UNIRPIX_DEV_PORT=5052 python app_sqlite_dev.py
```

No Windows PowerShell:

```powershell
$env:UNIRPIX_DEV_PORT = "5052"
python app_sqlite_dev.py
```

Nesse caso, acesse [http://127.0.0.1:5052](http://127.0.0.1:5052).

## Testes

Com as dependências instaladas e o ambiente virtual ativado, execute toda a suíte:

```sh
python -m unittest discover -s . -p "test_*.py" -v
```

Os testes usam dados fictícios e arquivos ou bancos temporários.

## Aviso sobre o ambiente

`app_sqlite_dev.py` inicia o servidor Flask de desenvolvimento somente em `127.0.0.1`. Ele cria o banco SQLite e os arquivos de sessão em uma pasta temporária e os descarta quando o processo é encerrado. Use uma conta fictícia e créditos de teste. Esta configuração é destinada a estudo local, não a produção.
