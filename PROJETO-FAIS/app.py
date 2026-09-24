from flask import Flask, render_template, request, jsonify
import csv, io, re
from decimal import Decimal, InvalidOperation
from datetime import datetime

app = Flask(__name__)


def money(v):
    if v is None:
        return Decimal("0")
    s = str(v).strip().replace("R$", "").replace(" ", "")
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", ".")
    try:
        return Decimal(s)
    except InvalidOperation:
        return Decimal("0")


def date_parse(v):
    if v is None:
        return None
    s = str(v).strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return None


def rows(file):
    raw = file.read().decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(raw)))


def norm(r):
    return {str(k).strip().lower(): (str(v).strip() if v is not None else "") for k, v in r.items()}


def same_person(p, f):
    keys = ["matricula", "cpf", "aluno"]
    for k in keys:
        a = p.get(k, "").lower().strip()
        b = f.get(k, "").lower().strip()
        if a and b and a == b:
            return True
    return False


def _extract_receipt_fields(text):
    values = {}
    if not text:
        return values

    text = text.replace("\r", "\n")

    aluno = re.search(r"(?i)(?:aluno|nome)\s*[:\-]?\s*([A-Za-zÀ-ÿ0-9 .'-]+)", text)
    if aluno:
        values["aluno"] = aluno.group(1).strip()

    matricula = re.search(r"(?i)(?:matr[ií]cula|matricula)\s*[:\-]?\s*([A-Za-z0-9/-]+)", text)
    if matricula:
        values["matricula"] = matricula.group(1).strip()

    cpf = re.search(r"(?i)cpf\s*[:\-]?\s*([0-9.\-]+)", text)
    if cpf:
        values["cpf"] = cpf.group(1).strip()

    valor = re.search(r"(?i)(?:valor|total|pagamento)\s*[:\-]?\s*R?\$?\s*([0-9]{1,3}(?:\.[0-9]{3})*,\d{2}|[0-9]+,\d{2}|[0-9]+\.\d{2})", text)
    if valor:
        values["valor"] = valor.group(1).strip()

    data = re.search(r"(?i)(?:data|emissao|pagamento)\s*[:\-]?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", text)
    if data:
        values["data"] = data.group(1).strip()

    return values


def extract_receipt_data(file):
    if file is None:
        return {}

    raw = file.read()
    if not raw:
        return {}
    file.seek(0)

    name = (file.filename or "").lower()
    text = ""
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = raw.decode("latin-1")
        except Exception:
            text = ""

    payload = _extract_receipt_fields(text)
    if payload:
        return payload

    # fallback por nome do arquivo quando o comprovante não está em texto legível
    file_match = re.search(r"(?i)(?:aluno|nome|matricula|cpf|valor|data)[-_ ]*([A-Za-z0-9À-ÿ .'-]+)", name)
    if file_match:
        return {"aluno": file_match.group(1).strip() or "Aluno"}

    return {}


def analyze(payments, financials, comprovante=None):
    out = []
    receipt = extract_receipt_data(comprovante)
    receipt_value = money(receipt.get("valor")) if receipt.get("valor") else None
    receipt_dt = date_parse(receipt.get("data")) if receipt.get("data") else None

    for p0 in payments:
        p = norm(p0)
        value = money(p.get("valor"))
        dt = date_parse(p.get("data_pagamento", ""))
        candidates = []

        for f0 in financials:
            f = norm(f0)
            if not same_person(p, f):
                continue
            status = f.get("status", "").lower()
            fv = money(f.get("valor"))
            if status in ("aberto", "em aberto", "pendente") and abs(value - fv) <= Decimal("0.01"):
                candidates.append(f)

        target = None
        if dt:
            for f in candidates:
                comp = f.get("competencia", "")
                m = re.match(r"(\d{1,2})/(\d{4})", comp)
                if m and int(m.group(1)) == dt.month and int(m.group(2)) == dt.year:
                    target = f
                    break
        if not target and candidates:
            target = candidates[0]

        auto_baixa = False
        if receipt:
            if same_person(p, receipt) or p.get("matricula") == receipt.get("matricula") or p.get("cpf") == receipt.get("cpf"):
                if receipt_value is not None and abs(value - receipt_value) <= Decimal("0.01"):
                    if receipt_dt is not None and dt is not None and abs((dt.date() - receipt_dt.date()).days) <= 3:
                        auto_baixa = True

        if not target:
            action = "ANÁLISE MANUAL"
            cls = "manual"
            detail = "Nenhuma mensalidade em aberto compatível foi localizada."
        elif auto_baixa:
            action = "BAIXA AUTOMÁTICA"
            cls = "ok"
            detail = "Comprovante de pagamento localizado e compatível com a mensalidade. Baixa automática sugerida."
        elif dt and target.get("competencia") == f"{dt.month:02d}/{dt.year}":
            action = "BAIXA / CONFIRMAR"
            cls = "ok"
            detail = f"Pagamento compatível com a competência {target.get('competencia')}."
        else:
            action = "SUGERIR REMANEJAMENTO"
            cls = "warn"
            detail = f"Pagamento compatível com {target.get('competencia')}; conferir a competência antes de remanejar."

        out.append({
            "aluno": p.get("aluno") or p.get("matricula") or p.get("cpf") or "Não informado",
            "matricula": p.get("matricula", ""),
            "valor": f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
            "data": dt.strftime("%d/%m/%Y") if dt else p.get("data_pagamento", ""),
            "competencia": target.get("competencia", "—") if target else "—",
            "action": action,
            "class": cls,
            "detail": detail,
            "auto_baixa": auto_baixa,
        })

    return out


@app.route("/")
def home():
    return render_template("index.html")


@app.post("/analisar")
def analisar():
    try:
        pf = request.files.get("pagamentos")
        ff = request.files.get("ficha")
        comprovante = request.files.get("comprovante")

        if not pf or not ff:
            return jsonify({"error": "Envie os dois arquivos CSV."}), 400

        result = analyze(rows(pf), rows(ff), comprovante=comprovante)
        return jsonify({"result": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


if __name__ == "__main__":
    app.run(debug=True)
