# FAIS — Fatecie Finance AI

Protótipo para análise de pagamentos e conferência de mensalidades via CSV e comprovante de pagamento.

## Como executar

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Acesse:

```text
http://127.0.0.1:5000
```

## Funcionalidades

- upload de pagamentos CSV
- upload de ficha financeira CSV
- upload opcional de comprovante de pagamento
- identificação automática de aluno, matrícula, valor e data
- baixa automática sugerida quando o comprovante confirma o pagamento
