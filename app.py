import os
import re
import string
import hashlib
from flask_cors import CORS
from datetime import datetime, timezone
from urllib.parse import urlparse, quote_plus
import redis 


from flask import Flask, jsonify, redirect, render_template, request
from flask_sqlalchemy import SQLAlchemy

BASE62 = string.ascii_letters + string.digits
CODE_LEN = 7

app = Flask(__name__)
#Allow CORS, only local at the moment (Front end Running on localhost:8080) Need to change for production
CORS(app) #currently overidden to allow all requests

db_connection_string = os.getenv("DB_CONNECTION", "NOT FOUND")
print(repr(os.getenv("db_connection_string")))
params = quote_plus(db_connection_string)

app.config["SQLALCHEMY_DATABASE_URI"] = 'mssql+pyodbc://?odbc_connect={}'.format(params)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")

db = SQLAlchemy(app)

REDIS_HOST = os.getenv("REDIS_HOST")
REDIS_PORT = os.getenv("REDIS_PORT")
PASSWORD = os.getenv("REDIS_PASSWORD")
THRESHOLD=5

redis_client = redis.Redis(
    host = REDIS_HOST,
    port = REDIS_PORT,
    ssl=True,
    ssl_cert_reqs=None,  # try temporarily for debugging
    decode_responses=True,
    socket_connect_timeout=5,
    socket_timeout=5,
    password = PASSWORD
)

class Link(db.Model):
    __tablename__ = "links"
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(32), unique=True, nullable=False, index=True)
    url = db.Column(db.Text, nullable=False)
    url_hash = db.Column(db.String(64), unique=True, nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    clicks = db.Column(db.Integer, nullable=False, default=0)

def is_valid_url(raw: str) -> bool:
    try:
        p = urlparse(raw)
        return p.scheme in ("http", "https") and bool(p.netloc)
    except Exception:
        return False

ALIAS_RE = re.compile(r"^[A-Za-z0-9_-]{3,32}$")

def base62_encode_int(n: int) -> str:
    if n == 0:
        return BASE62[0]
    out = []
    base = len(BASE62)
    while n > 0:
        n, r = divmod(n, base)
        out.append(BASE62[r])
    return "".join(reversed(out))

def stable_code_from_url(url: str, length: int = CODE_LEN) -> str:
    h = hashlib.sha256(url.encode("utf-8")).digest()
    n = int.from_bytes(h[:8], "big")
    code = base62_encode_int(n)
    if len(code) < length:
        code = (BASE62[0] * (length - len(code))) + code
    return code[:length]

def build_short_url(code: str) -> str:
    if PUBLIC_BASE_URL:
        return f"{PUBLIC_BASE_URL}/r/{code}"
    return f"/r/{code}"


@app.post("/api/shorten")
def api_shorten():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    custom = (data.get("custom_code") or "").strip()

    if not url:
        return jsonify(error="Missing 'url'"), 400
    if not is_valid_url(url):
        return jsonify(error="Invalid URL"), 400

    url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()

    if custom:
        if not ALIAS_RE.match(custom):
            return jsonify(error="Invalid custom_code"), 400
        if Link.query.filter_by(code=custom).first():
            return jsonify(error="custom_code taken"), 409
        link = Link(code=custom, url=url, url_hash=url_hash)
        db.session.add(link)
        db.session.commit()
        return jsonify(code=link.code, short_url=build_short_url(link.code)), 201
    
    existing.code = redis_client.get(url_hash)
    if(not existing.code):
        existing = Link.query.filter_by(url_hash=url_hash).first()
    if existing:
        return jsonify(code=existing.code, short_url=build_short_url(existing.code)), 200

    code = stable_code_from_url(url)
    attempt = 0
    while Link.query.filter_by(code=code).first():
        attempt += 1
        code = stable_code_from_url(f"{url}#{attempt}", CODE_LEN + attempt // 5)

    link = Link(code=code, url=url, url_hash=url_hash)
    db.session.add(link)
    db.session.commit()

    return jsonify(code=link.code, short_url=build_short_url(link.code)), 201

@app.get("/r/<code>")
def follow(code: str):
    url = redis_client.get(hash)
    if(url is not None):
        url_update = Link.query.filter_by(code=code).first()
        url_update.clicks += 1       
        db.session.commit()
        return redirect(url, code=303)
    link = Link.query.filter_by(code=code).first()
    if not link:
        return jsonify(error="Not found"), 404
    link.clicks += 1
    db.session.commit()
    if(link.clicks >= THRESHOLD):
        redis_client.set(link.url_hash,link.code) 
        redis_client.set(link.code,link.url) 
    return redirect(link.url, code=302)

@app.get("/api/<code>")
def info(code: str):
    link = Link.query.filter_by(code=code).first()
    if not link:
        return jsonify(error="Not found"), 404
    return jsonify(
        code=link.code,
        url=link.url,
        clicks=link.clicks,
        created_at=link.created_at.isoformat()
    )
## helthcheck endpoint
@app.get("/health")
def health():
    return jsonify(status="ok"), 200

def fill_redis():
    results = Link.query.filter(Link.clicks > THRESHOLD).all()
    print(results)
    for result in results:
        print(f"Result : {result}\n")
        print(f"hash value : {result.url_hash}\n")
        print(f"code value : {result.code}\n")
        print(f"url value : {result.url}\n")
        redis_client.set(result.url_hash,result.code)    
        redis_client.set(result.code,result.url)
        #redis_client.jset()
    print("Done")

with app.app_context():
    print("Filling redis")
    fill_redis()  

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    port = int(os.getenv("PORT", "8001"))
    app.run(host="0.0.0.0", port=port, debug=True)
