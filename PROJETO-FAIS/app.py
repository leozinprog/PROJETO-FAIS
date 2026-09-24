from flask import Flask, render_template, request, jsonify
import csv, io, os, re, sqlite3
from decimal import Decimal, InvalidOperation
from datetime import datetime

try:
    import pytesseract
except ImportError:
    pytesseract = None

try:
    from PIL import Image
except ImportError:
    Image = None

try:
    import fitz
except ImportError:
    fitz = None

app = Flask(__name__)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "finance.db")


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS baixas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            aluno TEXT,
            matricula TEXT,
            cpf TEXT,
            valor TEXT,
            competencia TEXT,
            data_pagamento TEXT,
            status TEXT DEFAULT 'baixado',
            comprovante TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


init_db()


def save_baixa(item):
    if not item or not item.get("auto_baixa"):
        return None

    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO baixas (aluno, matricula, cpf, valor, competencia, data_pagamento, status, comprovante) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            item.get("aluno", ""),
            item.get("matricula", ""),
            "",
            item.get("valor", ""),
            item.get("competencia", ""),
            item.get("data", ""),
            "baixado",
            "comprovante_anexado",
        ),
    )
    conn.commit()
    conn.close()
    return True


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


def ocr_receipt_text(file):
    if file is None or pytesseract is None or Image is None:
        return ""

    raw = file.read()
    if not raw:
        return ""
    file.seek(0)

    name = (file.filename or "").lower()

    try:
        if name.endswith(".pdf"):
            if fitz is None:
                return ""
            doc = fitz.open(stream=raw, filetype="pdf")
            if doc.page_count <= 0:
                return ""
            page = doc[0]
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
            image = Image.open(io.BytesIO(pix.tobytes("png")))
        elif name.endswith((".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff")):
            image = Image.open(io.BytesIO(raw)).convert("RGB")
        else:
            return ""

        text = pytesseract.image_to_string(image, config="--psm 6")
        return text or ""
    except Exception:
        return ""


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

    if not text:
        text = ocr_receipt_text(file)

    payload = _extract_receipt_fields(text)
    if payload:
        return payload

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
        for item in result:
            if item.get("auto_baixa"):
                save_baixa(item)

        return jsonify({"result": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.get("/baixas")
def listar_baixas():
    conn = sqlite3.connect(DB_PATH)
    registros = conn.execute(
        "SELECT id, aluno, matricula, valor, competencia, data_pagamento, status, created_at FROM baixas ORDER BY id DESC"
    ).fetchall()
    conn.close()

    return jsonify({
        "baixas": [
            {
                "id": row[0],
                "aluno": row[1],
                "matricula": row[2],
                "valor": row[3],
                "competencia": row[4],
                "data_pagamento": row[5],
                "status": row[6],
                "created_at": row[7],
            }
            for row in registros
        ]
    })


if __name__ == "__main__":
    app.run(debug=True)
