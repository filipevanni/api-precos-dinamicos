import os
import re
import csv
import io
import requests
import unidecode
from flask import Flask, request, jsonify

app = Flask(__name__)

# =========================================
# Normalização de nomes
# =========================================
def norm_nome(txt: str) -> str:
    if txt is None:
        return ""
    t = unidecode.unidecode(txt.lower())
    t = re.sub(r"\bde\b", " ", t)   # remove 'de' como palavra
    t = t.replace("-", " ")
    t = " ".join(t.split())
    return t

# =========================================
# Util: URL CSV do Google Sheets
# Aceita pubhtml e converte para CSV
# =========================================
def to_csv_url(url: str) -> str:
    if not url:
        return ""
    if "output=csv" in url:
        return url
    if "/pubhtml" in url:
        url = url.replace("/pubhtml", "/pub")
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}output=csv"
    return url

# =========================================
# Parse de preço tolerante
# "R$ 1.497,00" -> 1497 (int)
# =========================================
def parse_preco(v) -> int | None:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    s = s.replace(".", "")
    s = s.replace(",", ".")
    try:
        return int(round(float(s)))
    except Exception:
        # fallback: pega só dígitos
        digs = "".join(ch for ch in s if ch.isdigit())
        return int(digs) if digs else None

# =========================================
# Catálogo de materiais (preços unitários)
# norm_name -> {"preco": int, "display": str}
# =========================================
MATERIAIS_URL = os.getenv("MATERIAIS_URL", "").strip()  # coloque a URL CSV aqui nas envs do Render
CATALOGO = {}

def carregar_catalogo():
    global CATALOGO
    CATALOGO.clear()
    url = to_csv_url(MATERIAIS_URL)
    if not url:
        app.logger.warning("MATERIAIS_URL não definido nas variáveis de ambiente.")
        return

    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
    except Exception as e:
        app.logger.error(f"Falha ao baixar planilha de materiais: {e}")
        return

    content = r.content.decode("utf-8", errors="ignore")
    reader = csv.DictReader(io.StringIO(content))

    # tolera cabeçalhos com variações
    # esperamos pelo menos: materiais | preco
    linhas = 0
    lidas = 0
    for row in reader:
        linhas += 1
        mat = row.get("materiais") or row.get("material") or row.get("nome") or ""
        preco_raw = row.get("preco") or row.get("preço") or row.get("preco (r$)") or ""

        mat = str(mat).strip()
        if not mat:
            continue

        preco = parse_preco(preco_raw)
        if preco is None:
            continue

        key = norm_nome(mat)
        if not key:
            continue

        CATALOGO[key] = {"preco": preco, "display": mat}
        lidas += 1

    app.logger.info(f"Catálogo carregado: {lidas}/{linhas} linhas válidas. Materiais únicos: {len(CATALOGO)}")

# carrega ao iniciar
carregar_catalogo()

# =========================================
# (Opcional) categorização simples
# Se tiver algum material “premium”, marca como Couro Premium; senão Casual Urbano
# Você pode ajustar a regra/Lista a seu gosto
# =========================================
PREMIUM_KEYS = {
    norm_nome(n) for n in [
        "Couro de Jacaré", "Couro de Python", "Couro de Avestruz",
        "Couro de Pirarucu", "Couro de Elefante"
    ]
}

def classificar_categoria(keys_norm: list[str]) -> str:
    return "Couro Premium" if any(k in PREMIUM_KEYS for k in keys_norm) else "Casual Urbano"

# =========================================
# Endpoints
# =========================================
@app.route("/health")
def health():
    return jsonify({
        "ok": True,
        "materiais_catalogo": len(CATALOGO),
        "exemplo": "/preco?materiais=Couro Bovino, Couro de Tilápia, Jeans"
    })

@app.route("/materiais")
def materiais():
    # lista o catálogo carregado
    itens = sorted([v["display"] for v in CATALOGO.values()], key=lambda x: x.lower())
    return jsonify({"itens": itens, "total": len(itens)})

@app.route("/reload", methods=["POST"])
def reload():
    # recarrega o catálogo sem redeploy
    carregar_catalogo()
    return jsonify({"ok": True, "materiais_catalogo": len(CATALOGO)})

@app.route("/preco")
def preco():
    """
    GET /preco?materiais=A, B, C[, D ...]
    Calcula (soma dos preços unitários) / N e retorna inteiro.
    """
    materiais_str = (request.args.get("materiais") or "").strip()
    if not materiais_str:
        return jsonify({"erro": "Materiais não informados"}), 400

    # separa por vírgula
    itens = [p.strip() for p in materiais_str.split(",") if p.strip()]
    if not itens:
        return jsonify({"erro": "Nenhum material válido informado"}), 400

    # normaliza cada um e tenta achar no catálogo
    keys_norm = [norm_nome(p) for p in itens]
    desconhecidos = [itens[i] for i, k in enumerate(keys_norm) if k not in CATALOGO]

    if desconhecidos:
        return jsonify({
            "erro": "Alguns materiais não existem no catálogo",
            "materiais_desconhecidos": desconhecidos
        }), 404

    # soma dos preços / N
    soma = sum(CATALOGO[k]["preco"] for k in keys_norm)
    n = len(keys_norm)
    preco_final = round(soma / n)  # arredonda para inteiro

    categoria = classificar_categoria(keys_norm)

    return jsonify({
        "materiais": itens,            # ecoa como veio (bonitinho)
        "preco": int(preco_final),     # inteiro
        "categoria": categoria,
        "detalhes": {
            "soma_precos_unitarios": soma,
            "quantidade_materiais": n
        }
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
